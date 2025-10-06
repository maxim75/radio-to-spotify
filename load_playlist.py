import datetime
import pandas as pd 
import logging
import os

aws_api_key = os.environ.get("AWS_API_KEY")

logging.basicConfig(filename='/var/log/load_playlist.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Log messages
logging.info('This is an informational message')

import datetime

def main():
    print(f" Python script executed at {datetime.datetime.now()}")

    if aws_api_key:
        logging.info(f"AWS_API_KEY {aws_api_key[:4]}")
    else:
        logging.info("AWS_API_KEY is not set")

    df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]})
    df.to_csv("/var/data/sample_output.csv", index=False)

if __name__ == "__main__":
    main()