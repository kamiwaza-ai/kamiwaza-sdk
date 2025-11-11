#!/usr/bin/env python3
"""
Generate test Parquet data for MinIO ingestion tests.

Creates:
1. A flat file with 10k rows and 8 columns
2. Partitioned files by countryCode and date
"""

import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import os
from datetime import datetime, timedelta
import random
import string
import argparse

# Parse command line arguments
parser = argparse.ArgumentParser(description='Generate test Parquet data')
parser.add_argument('--output-dir', default='test-data', help='Output directory for generated files')
args = parser.parse_args()

# Ensure output directory exists
output_dir = args.output_dir
os.makedirs(output_dir, exist_ok=True)

# Generate sample data for flat file
print("Generating flat file data...")
np.random.seed(42)
random.seed(42)

# Create 10k rows with 8 columns
n_rows = 10000

def random_string(length=10):
    """Generate a random string of given length."""
    return ''.join(random.choices(string.ascii_letters, k=length))

# Generate columns with different data types
data = {
    'id': np.arange(1, n_rows + 1),
    'customer_name': [f"Customer_{random_string(8)}" for _ in range(n_rows)],
    'product_id': np.random.randint(1000, 9999, size=n_rows),
    'quantity': np.random.randint(1, 100, size=n_rows),
    'price': np.round(np.random.uniform(10.0, 1000.0, size=n_rows), 2),
    'discount_pct': np.round(np.random.uniform(0, 0.5, size=n_rows), 2),
    'order_date': pd.date_range(start='2024-01-01', periods=n_rows, freq='5min'),
    'is_premium': np.random.choice([True, False], size=n_rows, p=[0.3, 0.7])
}

# Calculate total_amount
data['total_amount'] = np.round(
    data['quantity'] * data['price'] * (1 - data['discount_pct']), 2
)

# Create DataFrame
df_flat = pd.DataFrame(data)

# Write flat file
flat_file_path = os.path.join(output_dir, 'sales_data_10k.parquet')
df_flat.to_parquet(flat_file_path, engine='pyarrow', compression='snappy')
print(f"Created flat file: {flat_file_path}")
print(f"  Schema: {list(df_flat.columns)}")
print(f"  Rows: {len(df_flat)}")
print(f"  Size: {os.path.getsize(flat_file_path) / 1024 / 1024:.2f} MB")

# Generate partitioned data
print("\nGenerating partitioned data...")
country_codes = ['US', 'UK', 'CA', 'AU', 'DE', 'FR', 'JP', 'BR']
dates = pd.date_range(start='2025-01-01', end='2025-01-07', freq='D')

# Create partitioned dataset root
partitioned_root = os.path.join(output_dir, 'sales_partitioned')
os.makedirs(partitioned_root, exist_ok=True)

# Generate data for each partition
all_partitioned_data = []

for country in country_codes:
    for date in dates:
        # Generate 100-500 rows per partition
        n_partition_rows = np.random.randint(100, 500)
        
        partition_data = {
            'transaction_id': [f"{country}_{date.strftime('%Y%m%d')}_{i:04d}" 
                             for i in range(n_partition_rows)],
            'customer_id': np.random.randint(10000, 99999, size=n_partition_rows),
            'product_name': [f"Product_{random_string(6)}" for _ in range(n_partition_rows)],
            'amount': np.round(np.random.uniform(10.0, 500.0, size=n_partition_rows), 2),
            'currency': country if country != 'US' else 'USD',
            'timestamp': [date + timedelta(
                hours=np.random.randint(0, 24),
                minutes=np.random.randint(0, 60)
            ) for _ in range(n_partition_rows)],
            'countryCode': country,
            'date': date.strftime('%Y-%m-%d')
        }
        
        df_partition = pd.DataFrame(partition_data)
        all_partitioned_data.append(df_partition)
        
        # Create partition directory
        partition_dir = os.path.join(
            partitioned_root, 
            f"countryCode={country}", 
            f"date={date.strftime('%Y-%m-%d')}"
        )
        os.makedirs(partition_dir, exist_ok=True)
        
        # Write partition file
        partition_file = os.path.join(
            partition_dir, 
            f"part-{country}-{date.strftime('%Y%m%d')}-{random_string(8)}.parquet"
        )
        df_partition.drop(['countryCode', 'date'], axis=1).to_parquet(
            partition_file, 
            engine='pyarrow', 
            compression='snappy'
        )

# Create a combined dataset for testing
df_all_partitioned = pd.concat(all_partitioned_data, ignore_index=True)

# Write metadata files
print("\nWriting root metadata...")

# Create _metadata file (PyArrow schema)
schema = pa.Schema.from_pandas(df_all_partitioned.drop(['countryCode', 'date'], axis=1))
metadata_collector = []

# Walk through all partition files
for root, dirs, files in os.walk(partitioned_root):
    for file in files:
        if file.endswith('.parquet'):
            file_path = os.path.join(root, file)
            # Read the parquet file metadata
            pq_file = pq.ParquetFile(file_path)
            metadata_collector.append(pq_file.metadata)

# Write _common_metadata
common_metadata_path = os.path.join(partitioned_root, '_common_metadata')
pq.write_metadata(schema, common_metadata_path)

# Write _metadata with all file metadata
metadata_path = os.path.join(partitioned_root, '_metadata')
# Note: This is a simplified version. In production, you'd aggregate all metadata properly
pq.write_metadata(schema, metadata_path)

print(f"\nCreated partitioned dataset: {partitioned_root}")
print(f"  Partitions: countryCode={country_codes}, date={dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')}")
print(f"  Total rows: {len(df_all_partitioned)}")
print(f"  Schema: {list(df_all_partitioned.drop(['countryCode', 'date'], axis=1).columns)}")

# Create a SUCCESS marker file (common in Spark/Hadoop ecosystems)
success_file = os.path.join(partitioned_root, '_SUCCESS')
with open(success_file, 'w') as f:
    f.write("")

print("\nTest data generation complete!")
print(f"\nTo copy to MinIO, run:")
print(f"  mc alias set test-minio http://localhost:9100 minioadmin minioadmin")
print(f"  mc mb test-minio/kamiwaza-test-bucket")
print(f"  mc cp -r {output_dir}/* test-minio/kamiwaza-test-bucket/")
