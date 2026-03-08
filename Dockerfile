# Use an official Python runtime as a parent image
FROM python:3.12-slim-bookworm

# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-cache

# Copy the rest of the application code
COPY . .

# Expose port 8000
EXPOSE 8000

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"

# Run the application
CMD ["python", "api_server.py"]
