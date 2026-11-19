# Third-party software

- **htmx 1.9.12** (`app/static/htmx.min.js`), BSD 2-Clause, Copyright (c) 2020, Big Sky Software.
  Vendored unmodified so the page loads nothing from a CDN. https://htmx.org
- Python dependencies (FastAPI, Uvicorn, Requests, Beautiful Soup, python-multipart, NumPy) are installed from PyPI
  at image build time under their own licences.
- The image also installs **ffmpeg** (with libgme) from Debian. ffmpeg and Game Music Emu are LGPL-licensed.

This project is not affiliated with or endorsed by Zophar's Domain or Navidrome.
