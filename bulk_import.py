import psycopg2
import requests
import csv
from io import StringIO

def bulk_import_google_sheets():
    conn = None
    cur = None
    
    try:
        # Connect to PostgreSQL database
        conn = psycopg2.connect(
            dbname="creator_database", 
            user="postgres", 
            password="Ilovela92", 
            host="localhost"
        )
        cur = conn.cursor()

        # Fetch CSV data from Google Sheets
        google_sheet_url = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vRRdkbLTOrZcSpjOQbE5kGrDr43DZWMYvdVxA5zhPBZ4T212o4ZmsyzzU3sENPpyO_v1hrasJ3wArrn/pub?output=csv'
        response = requests.get(google_sheet_url)

        # Read CSV content
        csv_data = StringIO(response.text)
        reader = csv.reader(csv_data)
        next(reader)  # Skip the header row

        # Insert or update rows in the creators table
        for row in reader:
            try:
                # Assuming the table has the following columns:
                # (name, url, contact, category, description, model, budget, followers, platform, language, country, type, topics)
                cur.execute("""
                    INSERT INTO creators (name, url, contact, category, description, model, budget, followers, platform, language, country, type, topics)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name, url) 
                    DO UPDATE SET
                        contact = EXCLUDED.contact,
                        category = EXCLUDED.category,
                        description = EXCLUDED.description,
                        model = EXCLUDED.model,
                        budget = EXCLUDED.budget,
                        followers = EXCLUDED.followers,
                        platform = EXCLUDED.platform,
                        language = EXCLUDED.language,
                        country = EXCLUDED.country,
                        type = EXCLUDED.type,
                        topics = EXCLUDED.topics
                """, row)

            except Exception as e:
                print(f"Error processing row: {row}, Error: {e}")

        # Commit the transaction
        conn.commit()
        print("Data imported and updated successfully.")

    except Exception as e:
        print(f"Error: {e}")
        if conn:
            conn.rollback()

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# Run the bulk import
bulk_import_google_sheets()
