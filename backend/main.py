import os, io, re, json, base64, tempfile
import cv2
from PIL import Image
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

app = FastAPI(title="FoodVerse AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- helpers ----------
def image_to_data_url(raw: bytes) -> str:
    """Resize + normalise to JPEG, return base64 data URL."""
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_llm(prompt: str, images=None) -> dict:
    content = [{"type": "text", "text": prompt + "\n\nRespond with ONLY valid JSON. No markdown, no extra text."}]
    for url in images or []:
        content.append({"type": "image_url", "image_url": {"url": url}})
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=0.2,
            max_tokens=4000,
        )
        text = resp.choices[0].message.content or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI error: {e}")


def video_to_frames(raw: bytes, n: int = 3):
    """Extract n evenly spaced frames from a video as data URLs."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp.write(raw)
    tmp.close()
    frames = []
    try:
        cap = cv2.VideoCapture(tmp.name)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for i in range(n):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n))
            ok, frame = cap.read()
            if ok:
                ok2, jpg = cv2.imencode(".jpg", cv2.resize(frame, (768, 432)))
                if ok2:
                    frames.append("data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode())
        cap.release()
    finally:
        os.remove(tmp.name)
    return frames


# ---------- routes ----------
@app.get("/api/health")
def health():
    return {"status": "online"}


@app.post("/api/food-recognition")
async def food_recognition(image: UploadFile = File(...), description: str = Form("")):
    img = image_to_data_url(await image.read())
    prompt = f"""You are a nutrition expert. Identify the dish in the image. User note: "{description}".
Return JSON with keys: dish_name (string), calories (number), protein_g (number), carbs_g (number),
fats_g (number), health_score (integer 0-100), health_evaluation (2 sentences), recommendations (list of 3 short healthier alternatives or tips)."""
    return ask_llm(prompt, [img])


@app.post("/api/freshness")
async def freshness(image: UploadFile = File(...), stored_for: str = Form(""),
                    storage_type: str = Form("Room Temp"), sensory_notes: str = Form("")):
    img = image_to_data_url(await image.read())
    prompt = f"""You are a food safety inspector. Assess the freshness of the food in the image.
Stored for: "{stored_for}". Storage: "{storage_type}". Smell/texture notes: "{sensory_notes}".
Return JSON with keys: food_name (string), status ("Fresh" or "Moderately Fresh" or "Spoiled"),
confidence (integer 0-100), safety_status ("SAFE" or "CAUTION" or "UNSAFE"),
detected_signs (list of short strings), reasoning (2 sentences), advice (one sentence)."""
    return ask_llm(prompt, [img])


@app.post("/api/hygiene")
async def hygiene(file: UploadFile = File(...),
                  rules: str = Form("Gloves mandatory. Hairnets required. No text on phone.")):
    raw = await file.read()
    if (file.content_type or "").startswith("video"):
        images = video_to_frames(raw)
        if not images:
            raise HTTPException(status_code=400, detail="Could not read frames from video")
    else:
        images = [image_to_data_url(raw)]
    prompt = f"""You are a kitchen hygiene auditor. These are frames from kitchen footage.
Compliance rules: "{rules}". Report only violations you can actually see.
Return JSON with keys: overall_score (integer 0-100), floor_cleanliness (integer 0-100),
alerts (list of objects with: title, severity "HIGH" or "MEDIUM" or "LOW", location, evidence),
summary (2 sentences). If everything is compliant, alerts is an empty list."""
    return ask_llm(prompt, images)


@app.post("/api/recipe")
async def recipe(description: str = Form(""), image: UploadFile = File(None)):
    if not description and not image:
        raise HTTPException(status_code=400, detail="Provide ingredients text or a photo")
    images = [image_to_data_url(await image.read())] if image else []
    prompt = f"""You are a chef. Create a complete recipe. User request: "{description}".
If an image is attached, use the dish or ingredients visible in it.
Return JSON with keys: title (string), cook_time (string), ingredients (list of strings with quantities),
steps (list of clear step strings)."""
    return ask_llm(prompt, images)


class Profile(BaseModel):
    age: int = Field(ge=5, le=100)
    gender: str = "Female"
    height_cm: float = Field(ge=80, le=250)
    weight_kg: float = Field(ge=20, le=300)
    activity_level: str = "Moderate"
    goal: str = "Maintenance"
    diet_type: str = "Standard"
    cuisine: str = "Global"
    allergies: str = ""
    medical_conditions: str = ""
    food_history: str = ""


@app.post("/api/meal-plan")
def meal_plan(p: Profile):
    # Mifflin-St Jeor BMR -> TDEE -> goal adjustment
    bmr = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age + (5 if p.gender.lower() == "male" else -161)
    factors = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very active": 1.9}
    tdee = bmr * factors.get(p.activity_level.lower(), 1.55)
    goal = p.goal.lower()
    target = tdee - 500 if "loss" in goal else tdee + 300 if "gain" in goal else tdee
    target = round(target / 10) * 10
    protein = round(p.weight_kg * (1.6 if ("loss" in goal or "gain" in goal) else 1.2))
    water = round(p.weight_kg * 0.033, 1)

    prompt = f"""You are a registered dietitian. Create a 1-day meal plan and a 7-day overview.
Profile: age {p.age}, {p.gender}, {p.height_cm} cm, {p.weight_kg} kg, activity {p.activity_level}.
Goal: {p.goal}. Diet: {p.diet_type}. Cuisine: {p.cuisine}.
ALLERGIES (never include these foods): "{p.allergies}". Medical conditions: "{p.medical_conditions}".
Recent food history: "{p.food_history}".
Daily target: {target} kcal, protein about {protein} g.
Return JSON with keys:
meals (list of 5 objects: type [Breakfast, Mid Morning Snack, Lunch, Evening Snack, Dinner], name, description,
 ingredients (list), calories, protein_g, carbs_g, fats_g, note),
weekly_overview (list of 7 objects: day [Mon..Sun], summary),
coach (object: consistency, suggestions (list of 3), message)."""
    plan = ask_llm(prompt)
    plan["daily_calories"] = target
    plan["protein_target_g"] = protein
    plan["hydration_liters"] = water
    return plan



from fastapi.responses import FileResponse

FRONTEND_FILE = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")


@app.get("/")
def index():
    return FileResponse(FRONTEND_FILE)