
# Use the official Python image as a base image
FROM python:3.9-slim-buster

# Set the working directory for the container
WORKDIR /app

# Copy the requirements file to the container (if needed)
COPY requirements.txt .

# Install any dependencies required for cleanup.py
RUN pip install --no-cache-dir -r requirements.txt

# Copy the cleanup script to the container
COPY cleanup.py .

# Set environment variables for the database
ENV POSTGRES_USER=ahmettugsuz
ENV POSTGRES_DB=bus_data
ENV POSTGRES_PASSWORD=bus_finland


# Command to run the cleanup script
CMD ["python", "cleanup.py"]
