import pdfplumber
import pandas as pd
import os
import re

def extract_last_table_as_text(pdf_path="./CAROLINE FEBRUARY/1.PDF", k=None):
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
    """Parse the extracted text and return a DataFrame with filtered columns."""
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


def main():
    target_dir = "./CAROLINE FEBRUARY/"
    pdf_files = [f for f in os.listdir(target_dir) if f.upper().endswith('.PDF')]

    for pdf_file in pdf_files:
        pdf_path = os.path.join(target_dir, pdf_file)
        print(f"\n=== {pdf_file} ===")
        df = extract_last_table_as_text(pdf_path, k=2)  # Example: keep last 2 months + Total
        if df is not None:
            print(df)
        else:
            print("Could not extract data")


if __name__ == "__main__":
    main()