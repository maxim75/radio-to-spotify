FROM python:3.13-slim

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Copy the requirements.txt file into the container
COPY requirements.txt .

# Install the Python dependencies listed in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Set the working directory
WORKDIR /app

# Copy the Python script and crontab file
COPY load_playlist.py /app/load_playlist.py
COPY crontab /etc/cron.d/my_cronjob

# Give execute permission to the Python script
RUN chmod +x /app/load_playlist.py

# Set permissions for the crontab file
RUN chmod 0644 /etc/cron.d/my_cronjob

# Create a log file for cron output
RUN touch /var/log/cron_output.log

# Start cron in the foreground and keep the container alive
CMD ["cron", "-f"]