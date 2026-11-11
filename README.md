# Children's App Safety Audit

A Streamlit application for static, rule-based screening of decoded Android XML layouts for 15 potential dark-pattern indicators. Parent-facing explanations are separated from researcher-oriented CSV evidence.

## Run locally

1. Install Python 3.10 or later.
2. In this folder, create and activate a virtual environment (optional but recommended).
3. Run `python -m pip install -r requirements.txt`.
4. Run `streamlit run app.py`.
5. Open the local URL printed by Streamlit, typically `http://localhost:8501`.

## Input preparation

Obtain an APK you are authorized to inspect. Use Apktool to decode resources, for example:

```bash
apktool d your_app.apk -o decoded_app
```

ZIP the `decoded_app` directory (including `res/layout*` and `res/values*` when available), then upload the ZIP under the Scanner tab. A ZIP containing multiple app directories is also supported. JADX-GUI can be used to inspect APK contents but does not always provide suitable decoded XML resources.

## Outputs

- `pediatric_audit_master_report.csv`: eight-column researcher report with DP IDs and technical evidence.
- `pediatric_audit_report.html`: standalone parent-friendly report with expandable findings.

## Deploy on Streamlit Community Cloud

Upload `app.py`, `requirements.txt`, and this `README.md` to a GitHub repository. In Streamlit Community Cloud, create a new app from that repository, select your branch, and set the entrypoint to `app.py`.

## Important limitations

This is a static screening tool, not a behavioral test or a guarantee of safety. XML keyword matches may be benign, and dynamically generated interfaces or server-side behaviors may be missed. The SAFE/MILD RISK/UNSAFE thresholds are project-defined classifications. Review the findings in context before drawing conclusions.

## Testing

The core scanner was tested with the original script's three built-in demo apps, including CSV and HTML generation, and ZIP traversal protection. The full Streamlit browser interface still requires local or cloud runtime validation.
