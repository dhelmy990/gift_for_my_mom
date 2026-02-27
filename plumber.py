import pdfplumber

def extract_last_table(pdf_path = "./CAROLINE FEBRUARY/1.PDF"):
    with pdfplumber.open(pdf_path) as pdf:
        # Target the last page directly
         last_page = pdf.pages[-1]
         if last_page.rects:
            summary_rect = sorted(last_page.rects, key=lambda x: x['bottom'])[-1]
            
            # Crop the page to the area of this rectangle
            # bbox = (x0, top, x1, bottom)
            bbox = (summary_rect['x0'], summary_rect['top'], 
                     summary_rect['x1'], summary_rect['bottom'])
            cropped = last_page.crop(bbox)
            print(cropped.extract_text())
    return None

"""
Design Choice:
1. Targeted Extraction: We index [-1] to avoid parsing the entire document, 
   reducing memory overhead and processing time.
2. extract_tables(): This method uses a 'visual' algorithm to find table 
   borders. It is more robust than simple string scraping because it 
   preserves the cell relationship even with wrapped text (like 'Jan 2026').
3. Post-Processing: PDF tables often come out messy (None values or empty 
   strings). You will likely need to clean the list/dataframe after extraction.
"""

print(extract_last_table())