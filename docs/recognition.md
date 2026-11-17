# Face recognition, thresholds and liveness

## Pipeline

Every enrollment sample and every kiosk frame goes through the same steps ([`app/face/engine.py`](../app/face/engine.py), `analyze_frame`):

1. **Intake and validation** ([`imaging.py`](../app/face/imaging.py)). Only JPEG or PNG data URLs are accepted, with a byte limit (`MAX_IMAGE_BYTES`, 2 MB) and dimension limits (`MIN_IMAGE_DIM` 240 px … `MAX_IMAGE_DIM` 1920 px). Pillow reads the header before full decoding, and decompression bombs are refused. OpenCV decodes the image and downscales it to `PROCESSING_MAX_WIDTH` (640 px) for speed.
2. **Lighting check.** Mean grey level must be within `MIN_BRIGHTNESS`…`MAX_BRIGHTNESS` (50…215). Otherwise the worker sees "Too dark…" or "Overexposed…".
3. **Face detection.** dlib's **HOG + linear SVM** detector (`face_recognition.face_locations`, `model="hog"`, 1× upsampling) finds frontal and near-frontal faces. `cnn` is available with a CUDA build of dlib. The frame must contain **exactly one** face. Multiple faces are rejected because the system cannot know who is recording attendance.
4. **Face size and sharpness.** The face box must be ≥ `MIN_FACE_SIZE_PX` (80 px). Variance of the Laplacian on the face crop must be ≥ `MIN_SHARPNESS`, which catches motion blur and dirty lenses.
5. **Landmarks.** dlib's 68-point shape predictor locates jaw, eyes, nose and mouth. It is used to align the face for encoding and by the liveness heuristics.
6. **Encoding.** dlib's ResNet face-recognition model (the model behind `face_recognition.face_encodings`) maps the aligned face to a **128-dimensional descriptor**. dlib reports 99.38% on the LFW benchmark. That is a benchmark figure for adults photographed under web-photo conditions, **not** a prediction for your kiosk.

## Matching

[`app/services/recognition.py`](../app/services/recognition.py) does 1:N identification against all `active` encodings of `active` workers:

- **Face distance** is the Euclidean distance between two 128-d descriptors. For each worker, the smallest distance over their samples is taken.
- **Decision.** Let *d₁* be the best worker's distance and *d₂* the runner-up's:
  - *d₁* > `FACE_MATCH_THRESHOLD` (0.50) → **unknown**
  - *d₂* − *d₁* < `FACE_AMBIGUITY_MARGIN` (0.06) → **ambiguous**: two different workers are both close, so the system refuses to guess
  - otherwise → **match**
- **Sequences.** At the kiosk, all frames from one attempt are matched. Any ambiguous frame, or frames matching *different* workers, rejects the attempt. At least `RECOGNITION_MIN_MATCHED_FRAMES` (3) frames must match the same worker. The stored `check_in_distance` is the median distance of those frames.

### Distance is not a confidence percentage

A distance of 0.35 does **not** mean "65% confident" or any other probability. It is a geometric quantity whose relationship to error rates depends on the population, the camera and the lighting. Turning it into a probability would need calibration on representative, consented data, which this project does not have. The UI therefore shows distances only to administrators (enrollment reviews) and never shows a percentage.

### Threshold trade-offs

| Lower threshold (e.g. 0.40) | Higher threshold (e.g. 0.60, the `face_recognition` default) |
|---|---|
| Fewer **false matches** (worker A accepted as worker B) | Fewer **false rejections** (enrolled worker not recognised) |
| More retries and supervisor help, especially in poor light | Higher risk of mistaken identity, especially between relatives and look-alikes |

The default of **0.50** is stricter than the library default because a false match here means attendance, and therefore pay, credited to the wrong person. A false rejection only means a retry or a supervisor's manual entry. Tune the threshold with on-site evaluation ([docs/testing.md](testing.md)); do not guess.

`ENROLL_DUPLICATE_THRESHOLD` (0.45) works the same way at enrollment. A lower value flags fewer potential duplicate identities. A higher value flags more of them, including genuine look-alikes, which an admin then approves.

## Effects of lighting, pose and camera quality

| Factor | Effect | Mitigation |
|---|---|---|
| Backlight (window or sun behind the worker) | Face underexposed, detection fails, larger distances | Place the kiosk facing the light; add a front light; the brightness check gives guidance |
| Harsh overhead or side light | Shadows change the descriptor | Diffuse lighting; enroll under the kiosk's actual lighting |
| Pose (looking down at a phone, turned head) | HOG misses faces beyond about ±30–40° yaw; distances grow | Mount the camera at face height; enroll slight left/right/up/down samples |
| Low-resolution or noisy webcams | Small faces fail the size check; noise lowers sharpness | 720p webcam or better; worker about 0.5–1 m from the camera |
| Motion blur | Sharpness check fails | Prompt "hold still"; good light allows shorter exposures |
| Caps, helmets, masks, sunglasses, dust | Occluded features → unknown or ambiguous | Ask the worker to remove them at the kiosk; enroll in typical work attire only if it is consistent |
| Ageing, beard changes, weight change | Distances drift over months | Re-enroll periodically; add new samples, which are appended |

## Liveness challenge

When `LIVENESS_REQUIRED=true`, each attempt starts with `POST /kiosk/api/challenge`. The server picks a random challenge (`blink`, `turn_left`, `turn_right`) with `secrets.choice`. It stores a SHA-256 hash of a random 256-bit token, tied to the kiosk browser session, the mode (check-in or check-out) and an expiry (`LIVENESS_CHALLENGE_TTL_SECONDS`, 30 s). The browser captures 16 frames about 180 ms apart while the worker follows the instruction.

When frames are submitted, the token is consumed atomically (single use). Expired, replayed, wrong-session and wrong-mode tokens are rejected. Then:

- **Blink:** the eye aspect ratio (EAR, Soukupová & Čech 2016) is computed from the 6 landmarks per eye. The attempt passes if some frame has EAR ≤ 70% of the open-eye level, with open-eye frames both before and after it.
- **Head turn:** the nose-tip position between the jaw edges (yaw ratio ≈ 0.5 when frontal). The worker must face the camera first, then reach ≥ 0.12 past centre in the **requested** direction. Frames are un-mirrored: turning to the worker's own left moves the nose towards the image's right edge.

Because the challenge type is random and decided by the server after the worker steps up, a static photo, or a recording made for a different challenge, fails.

### Limits of liveness and proxy protection

This is a **basic deterrent, not anti-spoofing**:

- A **video replay** on a phone or tablet held up to the camera can show a blink or a head turn. An attacker with recordings of all three actions can choose the right one within the 30 s window.
- **Masks, deepfake video, or a virtual camera** driver feeding pre-recorded frames to the browser are not detected.
- A **cooperating impostor** of similar appearance can be accepted if their distance is under the threshold.
- The heuristics have **not been validated** against a presentation-attack dataset. Thresholds such as `LIVENESS_BLINK_RATIO` and `LIVENESS_YAW_DELTA` are reasonable starting points, not measured optima.

Production-grade presentation-attack detection (ISO/IEC 30107-3 tested) needs dedicated PAD models, and often depth, IR or multi-spectral cameras. Until then, place the kiosk where a supervisor or CCTV can see it, review `kiosk.attempt_rejected` audit events, and investigate patterns such as repeated `liveness_failed` for one worker.

Proxy-attendance protection in this system comes from **all** of the following together: one record per day, the recognition threshold and ambiguity refusal, the random liveness challenge, the exactly-one-face rule, kiosk-device enrollment, and the audit trail. No single mechanism should be relied on alone.
