"""Entry point.

    flask --app wsgi run            # Flask dev server on 127.0.0.1:5000
    python wsgi.py                  # same, honouring HOST/PORT from .env
"""
import logging

from app import create_app
from app.security import is_loopback

app = create_app()

if __name__ == "__main__":
    host, port = app.config["HOST"], app.config["PORT"]
    if not is_loopback(host):
        logging.getLogger(__name__).warning(
            "Binding to %s: the app is reachable from the network. Browsers only allow camera access "
            "on localhost or HTTPS, and non-local use needs HTTPS and additional hardening "
            "(see docs/security-and-privacy.md).", host)
    app.run(host=host, port=port, debug=False)
