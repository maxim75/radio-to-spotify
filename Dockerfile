FROM python:3.13

# Install cron
RUN apt-get update 
RUN apt-get install -y cron && rm -rf /var/lib/apt/lists/*

#RUN apt-get install gcc


# Copy the requirements.txt file into the container
COPY requirements.txt .

# Install the Python dependencies listed in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Set the working directory
WORKDIR /app

# Copy the Python script and crontab file
COPY *.py /app/
COPY crontab /etc/cron.d/my_cronjob

# Give execute permission to the Python script
RUN chmod +x /app/load_playlist.py

# Set permissions for the crontab file
RUN chmod 0644 /etc/cron.d/my_cronjob

# Create a log file for cron output
RUN touch /var/log/cron_output.log

# RUN cron

# Start cron in the foreground and keep the container alive
# CMD ["cron", "-f"]
CMD cron
# CMD ["python", "app.py"]
CMD ["uwsgi", "--http", "0.0.0.0:8000", "--master", "-p",  "4",  "-w", "app:app"]