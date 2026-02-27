import pdfplumber
import pandas as pd
import os
import re

DESIRED_ORDER = ["HB", "VLV", "VBN", "HMB", "HMR"]

def extract_last_table_as_df(pdf_path="./CAROLINE FEBRUARY/1.PDF", k=None, name : str = "default"):
    """
    Extract Total Room Nights and Room Revenue from the Grand Total section.

    Args:
        pdf_path: Path to the PDF file
        k: Number of rightmost month columns to keep (Total always included)
           k=0: Only Total column
           k=1: [rightmost month, Total]
           k=2: [2nd rightmost month, rightmost month, Total]
           k=None: Keep all columns

    Returns:
        pandas DataFrame with filtered columns
    """
    with pdfplumber.open(pdf_path) as pdf:
        last_page = pdf.pages[-1]
        if last_page.rects:
            summary_rect = sorted(last_page.rects, key=lambda x: x['bottom'])[-1]
            bbox = (summary_rect['x0'], summary_rect['top'],
                    summary_rect['x1'], summary_rect['bottom'])
            cropped = last_page.crop(bbox)
            text = cropped.extract_text()

            if text:
                return parse_table_text(text, k)
    return None


def parse_table_text(text, k=None):
    """Parse the extracted text and return a DataFrame with filtered columns.
      I think this isn't really how i'd do it but its ok.
    """
    lines = text.strip().split('\n')

    # Find the header line (contains month names like "Jan 2026")
    header_line = None
    for line in lines:
        if re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}', line):
            header_line = line
            break

    if not header_line:
        return None

    # Parse headers - extract month columns
    headers = re.findall(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}|Total)', header_line)

    # Parse data rows
    data = {}
    for line in lines:
        if line.startswith('Total Room Nights'):
            values = re.findall(r'[\d,]+(?:\.\d+)?', line)
            values = [int(v.replace(',', '')) for v in values]
            data['Total Room Nights'] = values
        elif line.startswith('Room Revenue'):
            values = re.findall(r'[\d,]+\.\d+', line)
            values = [float(v.replace(',', '')) for v in values]
            data['Room Revenue'] = values

    if not data:
        return None

    # Create DataFrame
    df = pd.DataFrame(data, index=headers).T

    # Apply k filter: keep rightmost k month columns + Total
    if k is not None:
        month_cols = [col for col in df.columns if col != 'Total']
        if k == 0:
            # Keep only Total
            df = df[['Total']]
        else:
            # Keep rightmost k months + Total
            cols_to_keep = month_cols[-k:] + ['Total']
            df = df[cols_to_keep]

    return df


def two_tablify(dataframes: dict):
    """
    Transform dict of hotel DataFrames into two tables:
    - One for Total Room Nights (TRN)
    - One for Room Revenue (RR)

    Each table has hotels as rows and months as columns.

    Returns:
        tuple: (trn_df, rr_df)
    """
    trn_rows = {}
    rr_rows = {}

    for hotel_name, df in dataframes.items():
        trn_rows[hotel_name] = df.loc['Total Room Nights']
        rr_rows[hotel_name] = df.loc['Room Revenue']

    trn_df = pd.DataFrame(trn_rows).T
    rr_df = pd.DataFrame(rr_rows).T

    # Sort columns: months chronologically, Total at end
    month_order = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                   'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}

    def sort_key(col):
        if col == 'Total':
            return (9999, 0)  # Total always last
        month = col.split()[0]
        year = int(col.split()[1])
        return (year, month_order.get(month, 0))

    sorted_cols = sorted(trn_df.columns, key=sort_key)
    trn_df = trn_df[sorted_cols]
    rr_df = rr_df[sorted_cols]

    # Sort rows (hotels) alphabetically
    print(trn_df.index)

    trn_df = trn_df.reindex(DESIRED_ORDER).drop(columns = ["Total"])
    rr_df = rr_df.reindex(DESIRED_ORDER).drop(columns = ["Total"])

    return trn_df, (1.1 * rr_df).round(2)

def main():
    target_dir = "./CAROLINE FEBRUARY/"
    pdf_files = [f for f in os.listdir(target_dir) if f.upper().endswith('.PDF')]

    data = {}
    

    for pdf_file in pdf_files:
        pdf_path = os.path.join(target_dir, pdf_file)
        eventual_col = pdf_file.split()[0]
        print(f"\n=== {eventual_col} ===")
        df = extract_last_table_as_df(pdf_path, k=2)  # Example: keep last 2 months + Total
        
        if df is not None:
            data[eventual_col] = df
            print(df)
        else:
            print("Could not extract data")

    trn_df, rr_df = two_tablify(data)
    print("\n=== Total Room Nights ===")
    print(trn_df)
    print("\n=== Room Revenue ===")
    print(rr_df)
   
            
   


if __name__ == "__main__":
    main()