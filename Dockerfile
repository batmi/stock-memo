# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=backend_app.py

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose port 5000 for the app
EXPOSE 5000

# Define volumes for persistent data
# db: SQLite database files
# logs: Application log files
# uploads: User uploaded images
# backup: Automatic backup files
VOLUME ["/app/db", "/app/logs", "/app/uploads", "/app/backup"]

# Run the application
CMD ["python", "backend_app.py"]
