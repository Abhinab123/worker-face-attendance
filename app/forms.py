from flask_wtf import FlaskForm
from wtforms import BooleanField, DateTimeLocalField, HiddenField, PasswordField, StringField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, Optional


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=256)])


class WorkerForm(FlaskForm):
    worker_code = StringField("Worker ID", validators=[DataRequired(), Length(max=32)],
                              description="Unique, e.g. W-1001. Letters, digits, '-' or '_'.")
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=120)])
    department = StringField("Department", validators=[DataRequired(), Length(max=80)])
    contractor = StringField("Contractor", validators=[DataRequired(), Length(max=120)])
    phone = StringField("Phone (optional)", validators=[Optional(), Length(max=20)])


class ReasonForm(FlaskForm):
    reason = TextAreaField("Reason", validators=[DataRequired(), Length(min=5, max=500)])


class DeleteFaceDataForm(FlaskForm):
    reason = TextAreaField("Reason", validators=[DataRequired(), Length(min=5, max=500)])
    confirm = BooleanField("I understand this permanently deletes this worker's face data", validators=[InputRequired()])


class StatusForm(FlaskForm):
    action = HiddenField(validators=[DataRequired()])
    reason = TextAreaField("Reason", validators=[Optional(), Length(max=500)])


class CorrectionForm(FlaskForm):
    check_in_at = DateTimeLocalField("Check-in (local time)", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])
    check_out_at = DateTimeLocalField("Check-out (local time, optional)", format="%Y-%m-%dT%H:%M", validators=[Optional()])
    reason = TextAreaField("Reason for correction (required)", validators=[DataRequired(), Length(max=500)])


class ManualRecordForm(CorrectionForm):
    worker_code = StringField("Worker ID", validators=[DataRequired(), Length(max=32)])


class DeviceForm(FlaskForm):
    name = StringField("Device name", validators=[DataRequired(), Length(min=2, max=80)],
                       description="e.g. 'Gate 1 tablet'")


class ActivateForm(FlaskForm):
    token = StringField("Activation code", validators=[DataRequired(), Length(max=128)])


class ResolveFlagForm(FlaskForm):
    decision = HiddenField(validators=[DataRequired()])
    note = TextAreaField("Resolution note", validators=[DataRequired(), Length(min=5, max=500)])


class EmptyForm(FlaskForm):
    """CSRF-only form for simple POST buttons."""
