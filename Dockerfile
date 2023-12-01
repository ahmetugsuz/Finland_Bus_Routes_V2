# Use the official Python image as a base image
FROM python:3.9-slim-buster

# Set the working directory for the container
WORKDIR /app

# Copy the requirements file to the container
COPY requirements.txt .

# Install the dependencies in the container
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code to the container
COPY . .

# Set environment variables
ENV FLASK_APP=app.py
ENV FLASK_DEBUG=1

# Set environment variables for the database
ENV POSTGRES_USER=ahmettugsuz
ENV POSTGRES_DB=bus_data
ENV POSTGRES_PASSWORD=bus_finland


# Expose the port that the application listens on
EXPOSE 5001

# Run the application
CMD ["python", "app.py"]
