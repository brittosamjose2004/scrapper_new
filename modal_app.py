import modal
from pydantic import BaseModel

MODEL_NAME = "google/gemma-1.1-7b-it"

image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install(
        "transformers>=4.41.0",
        "torch==2.3.0",
        "accelerate",
        "huggingface_hub",
        "fastapi[standard]"
    )
)

app = modal.App("brsr-gemma-server", image=image)

# Imports for the container
with image.imports():
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM

@app.cls(
    gpu="A10G",
    scaledown_window=300,
    secrets=[modal.Secret.from_name("my-huggingface-secret")]
)
class Model:
    @modal.enter()
    def initialize(self):
        from huggingface_hub import snapshot_download
        print("Downloading/Loading model...")
        snapshot_download(MODEL_NAME)
        
        print("Loading model into GPU with Transformers...")
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.bfloat16,
            device_map="auto"
        )
        print("Model loaded successfully!")

    @modal.method()
    def generate(self, prompt: str):
        input_ids = self.tokenizer(prompt, return_tensors="pt").to("cuda")
        
        outputs = self.model.generate(
            **input_ids,
            max_new_tokens=2048,
            do_sample=True,
            temperature=0.2,
            top_p=0.95
        )
        
        # Decode only the new tokens
        generated_text = self.tokenizer.decode(outputs[0][input_ids.input_ids.shape[1]:], skip_special_tokens=True)
        return generated_text

class QueryRequest(BaseModel):
    prompt: str

@app.function(
    timeout=600
)
@modal.fastapi_endpoint(method="POST")
def answer_question(item: QueryRequest):
    model = Model()
    response = model.generate.remote(item.prompt)
    return {"answer": response}
