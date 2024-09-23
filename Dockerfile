# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY . .

# Create a directory for logs (if it doesn't exist) and set permissions
RUN mkdir -p /app/logs && chmod 1777 /app/logs

# Create a mount point for the NAS folder
RUN mkdir -p /mnt/nas

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_ENV=development
ENV FLASK_DEBUG=1
ENV PYTHONUNBUFFERED=1

# Run the application with Flask development server for debugging
CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
