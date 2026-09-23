# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=backend_app.py
# 세션 서명 키를 볼륨(db/) 안에 둔다. 기본 위치(/app/.secret_key)는 이미지 레이어라
# 컨테이너를 다시 만들 때마다 새 키가 생기고 전원이 로그아웃된다.
# (SECRET_KEY 환경변수를 주면 그 값이 우선한다)
ENV SECRET_KEY_FILE=/app/db/.secret_key

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose port 9094 for the app
EXPOSE 9094

# Define volumes for persistent data
# db: SQLite database files
# logs: Application log files
# uploads: User uploaded images
# backup: Automatic backup files
VOLUME ["/app/db", "/app/logs", "/app/uploads", "/app/backup"]

# Run the application
CMD ["python", "backend_app.py"]
