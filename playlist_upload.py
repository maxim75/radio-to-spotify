import boto3
from botocore.exceptions import ClientError
import logging
import os

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION")

def upload_file_to_s3(file_name, bucket, object_name):

    BUCKET_NAME = bucket
    OBJECT_KEY = "test.txt"  # The desired name of the object in S3
    FILE_CONTENT = "This is the content of your S3 object."

    try:
        # Create an S3 client with explicit credentials
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )

        upload_response = s3_client.upload_file(file_name, bucket, object_name)

        logging.info(f"Object '{object_name}' successfully created in bucket '{bucket}'.")

    except ClientError as e:
        logging.info(f"Error creating object: {e}")
    except Exception as e:
        logging.info(f"An unexpected error occurred: {e}")