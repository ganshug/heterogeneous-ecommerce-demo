# =============================================================================
# IBM Power E-Cart — Dockerfile
#
# Base image: registry.redhat.io/ubi9/python-311:latest
#   Red Hat UBI9 Python 3.11 — multi-arch (amd64 + ppc64le)
#   No docker.io images used.
#
# ibm_db requires the IBM Data Server Driver (CLIDRIVER) which is downloaded
# automatically by the ibm_db pip package during installation.
#
# Build this image on an amd64 (Intel) node to produce an amd64 image.
# =============================================================================
FROM registry.redhat.io/ubi9/python-311:latest

# Install system dependencies required by ibm_db C extension
USER root
RUN dnf install -y gcc gcc-c++ make libxml2-devel python3-devel && \
    dnf clean all

# Switch back to non-root user for the app
USER 1001

WORKDIR /opt/app-root/src

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
# ibm_db will auto-download the IBM CLIDRIVER (~100MB) during install
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app.py .

# Expose port 8080
EXPOSE 8080

# Run with gunicorn — production WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "app:app"]