# Contributing

Thank you for helping. The library is used to keep track of living cultures, so
correctness matters more than features.

## Ways to help
- **Report a bug** or **suggest a feature** with the issue templates.
- **Share a plate photograph the analysis gets wrong** ("Plate photo for testing"
  template). Real photos are the most useful contribution to the image analysis.
- **Run a pilot** in your lab and send the anonymous research export from the
  feedback page (see the paper's evaluation section).
- **Code**: fork, branch, and open a pull request.

## Development
```bash
python -m pip install -r requirements.txt segno
python test_app.py      # main suite, about a minute
python test_demo.py     # public demo mode
python app.py           # http://localhost:5000
```
Rules that the code follows, and pull requests should too:
- Anything with a scientific or compliance consequence (a due date, a count, who
  did what) is worked out by fixed rules or entered by a person, never guessed.
- History is voided with a reason, never deleted.
- Every query is scoped to one lab (`tenant_id`); files are served only to the lab
  whose record points at them.
- Every fix comes with a test that fails without it.

## Releasing (maintainer)
See `docs/RELEASING.md`.
