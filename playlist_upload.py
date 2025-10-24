import boto3
from botocore.exceptions import ClientError
import logging
import os

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION")

def list_objects_in_bucket(bucket_name):
    """List all objects in an S3 bucket"""
    try:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
        
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        if 'Contents' in response:
            return [obj['Key'] for obj in response['Contents']]
        return []
    except Exception as e:
        logging.error(f"Error listing objects in bucket {bucket_name}: {e}")
        return []

def download_file_from_s3(bucket_name, object_name):
    """Download an object from S3 bucket and return its contents as a string"""
    try:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
        
        response = s3_client.get_object(Bucket=bucket_name, Key=object_name)
        file_content = response['Body'].read().decode('utf-8')
        return file_content
    except Exception as e:
        logging.error(f"Error downloading {object_name} from {bucket_name}: {e}")
        return None

def upload_file_to_s3(file_name, bucket, object_name):
    """Upload a file to an S3 bucket"""

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