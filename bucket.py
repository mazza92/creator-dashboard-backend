import boto3
from supabase import create_client, Client
import os

# AWS S3 credentials
s3 = boto3.client(
    's3',
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
    region_name='eu-west-3'
)

# Supabase credentials
supabase_url = "https://kyawgtojxoglvlhzsotm.supabase.co"  # Replace with your Supabase URL
supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt5YXdndG9qeG9nbHZsaHpzb3RtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczMzIzODY1OCwiZXhwIjoyMDQ4ODE0NjU4fQ.AugYLadvycrnfjgRu1DaVEnAGDopaV_KUMLjMoU8aDc"  # Replace with your Supabase service key

# Initialize Supabase client
supabase: Client = create_client(supabase_url, supabase_key)

# Bucket configuration
s3_bucket_name = 'appbucket1992'
supabase_bucket_name = 'my-supabase-bucket'  # Replace with your Supabase bucket name

# Local storage for downloaded files
local_directory = "./downloaded_files"
os.makedirs(local_directory, exist_ok=True)  # Ensure the directory exists

# List objects in the S3 bucket
objects = s3.list_objects_v2(Bucket=s3_bucket_name).get('Contents', [])

if not objects:
    print("No files found in the S3 bucket.")
else:
    for obj in objects:
        file_key = obj['Key']
        print(f"Processing file: {file_key}")

        try:
            # Step 1: Download file from S3
            file_data = s3.get_object(Bucket=s3_bucket_name, Key=file_key)['Body'].read()

            # Optional: Save the file locally
            local_file_path = os.path.join(local_directory, file_key)
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)  # Ensure subdirectories are created
            with open(local_file_path, 'wb') as f:
                f.write(file_data)
            print(f"Downloaded {file_key} to {local_file_path}")

            # Step 2: Upload file to Supabase
            response = supabase.storage.from_(supabase_bucket_name).upload(file_key, file_data)
            if response.get('error'):
                print(f"Error uploading {file_key} to Supabase: {response['error']}")
            else:
                print(f"Uploaded {file_key} to Supabase.")

        except Exception as e:
            print(f"Error processing file {file_key}: {e}")
