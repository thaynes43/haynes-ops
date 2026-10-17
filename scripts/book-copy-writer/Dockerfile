# Same official Python digest already reviewed for the authoring image.
FROM python:3.15-slim@sha256:2da676046630d7bf14c6764ae1143dc7fa0377a53f94bf5a642f0b214aca5bce

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /copy-writer
COPY scripts/book-copy-writer/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.txt \
    && python -c 'import psycopg; from psycopg import pq; assert psycopg.__version__ == "3.3.6"; assert pq.__impl__ == "binary"; print(pq.version())'
COPY kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_copies.py ./epub_copies.py
COPY kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_metadata.py ./epub_metadata.py
COPY scripts/book-copy-writer/book_copy_writer.py ./book_copy_writer.py
COPY scripts/book-copy-writer/bound_census.py ./bound_census.py
COPY scripts/book-copy-writer/proof_transport.py ./proof_transport.py
USER 1000:1000
ENTRYPOINT ["python", "/copy-writer/book_copy_writer.py"]
