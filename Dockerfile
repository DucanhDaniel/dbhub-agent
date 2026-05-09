FROM python:3.10-slim

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt uv && \
    uv pip install --system -e .

# Copy the rest of the application
COPY . .

# Expose port
EXPOSE 8000

# Run the FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
