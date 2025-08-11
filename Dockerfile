# Use official Python 3.11.9 slim image
FROM python:3.11.9-slim

# Set working directory inside container
WORKDIR /app

# Copy dependency files first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your code
COPY . .

# Expose port if your bot uses one (optional, mostly for web apps)
# EXPOSE 8080

# Default command to run your bot
CMD ["python", "bot.py"]
