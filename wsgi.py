"""WSGI entry point for a production server (gunicorn, waitress, ...).

`python app.py` uses waitress if installed, else Flask's development server, fine for
trying the app out on a lab network but isn't meant to face real traffic.
For real use, point a production server at this module instead:

    gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8000 wsgi:app

Keep one worker: the login rate limiter and the chat helper live in memory
in that one process.

init_db_if_needed() has to run here because app.py only calls it under its
own `if __name__ == "__main__"` block, which a WSGI server never executes.
"""
from app import app, start_up

start_up()
