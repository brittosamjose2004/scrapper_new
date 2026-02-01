from pydantic import BaseModel
print("Pydantic imported successfully")

class User(BaseModel):
    name: str

u = User(name="Test")
print(u)
