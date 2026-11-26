# Pediatric Dark Pattern Auditor

Streamlit application for static screening of decoded Android XML resources.

## Deployment
Upload app.py and requirements.txt to the repository root. In Streamlit Community Cloud select the repository, branch and app.py as the entry point.

## Usage
Parent Mode accepts one application; Researcher Mode accepts batch ZIP archives. Upload decoded XML resources, not raw APKs. Visual reconstructions are schematic, not runtime screenshots. Screening findings are potential indicators, not certified safety assessments.

## Local execution
`pip install -r requirements.txt`

`streamlit run app.py`
