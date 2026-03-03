import streamlit as st
import modal
import json
import time

st.set_page_config(page_title="IMAPC Tree - ESG Analyzer", layout="wide")

st.title("🌱 IMAPC Tree: Intelligent ESG Analyzer")
st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    company = st.text_input("Company Name", "Tata Steel")
    source = st.selectbox("Data Source", ["all", "news", "annualreports", "nse"])
    
    if st.button("🚀 Start Analysis"):
        with st.spinner(f"Agents are parsing {company}... This involves Scrapers, PDF OCR, and LLM Inference."):
            try:
                # connect to modal function
                f = modal.Function.from_name("imapctree-backend", "process_pipeline")
                
                # Call remote function
                result = f.remote(company, source)
                
                st.success("Pipeline Completed!")
                st.json(result)
                
            except Exception as e:
                st.error(f"Execution Failed: {e}")
                st.warning("Did you run 'modal deploy modal_app.py' first?")

with col2:
    st.header("Live Artifacts")
    st.info("Files are being saved to Modal Shared Volume '/data'. Google Drive Sync is pending Auth.")
    
    st.markdown("### Status Console")
    st.code("Waiting for job...", language="bash")

st.markdown("---")
st.caption("Powered by Modal.com & HuggingFace Gemma")
