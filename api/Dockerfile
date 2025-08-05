# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables
ENV APP_HOME=/app
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
# Set the port the container will listen on (Cloud Run also injects this)
ENV PORT=8080
# Python specific flags
ENV PYTHONUNBUFFERED=True

WORKDIR $APP_HOME

# Install dependencies
COPY requirements.txt .
# Using --no-cache-dir reduces image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose the port the app runs on
# Note: EXPOSE is documentation; it doesn't actually require the shell form,
# but it's good practice to ensure it matches the variable Gunicorn uses.
# Using the variable here is fine, but exposing the default 8080 directly is also clear.
EXPOSE ${PORT}

# Run the application using gunicorn when the container launches
# Use the SHELL FORM of CMD to allow $PORT substitution
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "-b", "0.0.0.0:8080", "app:app"]
