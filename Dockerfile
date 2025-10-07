FROM python:3.13

# Copy the requirements.txt file into the container
COPY requirements.txt .

# Install the Python dependencies listed in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Set the working directory
WORKDIR /app

# Copy the Python script and crontab file
COPY *.py /app/

# CMD ["python", "app.py"]
CMD ["uwsgi", "--http", "0.0.0.0:8001", "--master", "-p",  "4",  "-w", "app:app"]