import streamlit as st
import asyncio
import json
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
from msrest.authentication import CognitiveServicesCredentials
from openai import AzureOpenAI
import os
import zipfile
import re
from datetime import datetime

# Configurar las credenciales de Azure
AZURE_ENDPOINT = "https://iacdemoaduanas.cognitiveservices.azure.com/"
AZURE_KEY = "e44dceb20f40469291dd107c2689e556"  # Cambia por tu API Key real
AZURE_OPENAI_ENDPOINT = "https://iac-demo-aduanas.openai.azure.com/"  # Coloca tu endpoint de Azure OpenAI
AZURE_OPENAI_KEY = "e68adbe619e241f7bb9c9d25389743d2"  # Coloca tu clave de Azure OpenAI

# Configurar cliente de Azure Computer Vision
cv_client = ComputerVisionClient(AZURE_ENDPOINT, CognitiveServicesCredentials(AZURE_KEY))

# Configurar cliente de Azure OpenAI
openai_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_KEY,
    api_version="2024-02-01"
)

def normalizar_fecha(fecha_str):
    """
    Esta función recibe una fecha en formato de string y la convierte en un objeto datetime.
    Detecta diferentes formatos de fecha como "día/mes/año", "día-mes-año", y "día.mes.año".
    """
    formatos_fecha = [
        r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})",  # Formato genérico (día/mes/año o día-mes-año o día.mes.año) 
    ]
    # Intentamos buscar un formato de fecha que coincida
    for formato in formatos_fecha:
        match = re.match(formato, fecha_str)
        if match:
            dia, mes, año = match.groups()

            # Si el año está en formato corto (por ejemplo, '21'), lo expandimos a '2021'
            if len(año) == 2:
                año = "20" + año if int(año) < 50 else "19" + año  # Se ajusta según el siglo más probable

            # Convertimos a objeto datetime
            try:
                return datetime(int(año), int(mes), int(dia))
            except ValueError:
                return None

    # Si no se pudo interpretar la fecha
    return None

# Función para extraer texto de PDF usando OCR de Azure
async def ocr_with_azure(file_stream, client):
    """Extraer texto de un PDF usando Azure OCR."""
    read_response = client.read_in_stream(file_stream, raw=True)
    read_operation_location = read_response.headers["Operation-Location"]
    operation_id = read_operation_location.split("/")[-1]

    while True:
        read_result = client.get_read_result(operation_id)
        if read_result.status not in ['notStarted', 'running']:
            break
        await asyncio.sleep(30)

    if read_result.status == OperationStatusCodes.succeeded:
        extracted_text = ""
        for text_result in read_result.analyze_result.read_results:
            for line in text_result.lines:
                extracted_text += line.text + " "
        return extracted_text.strip()

    return None

# Función para limpiar el texto JSON de respuestas del modelo
def clean_json_text(json_text):
    """Limpiar texto JSON para quitar caracteres no deseados."""
    return json_text.strip().strip('```').strip('json').strip('```')

# Función para convertir el texto en JSON usando Azure OpenAI
def parse_as_json(text, json_template):
    """Convertir el texto OCR en un JSON usando el modelo de Azure OpenAI."""
    messages = [
        {"role": "system", "content": "You are an expert in data formatting and validation."},
        {"role": "user", "content": (
            "Convert the following text into a JSON object that **must exactly match** the structure provided in the template:\n"
            f"{json_template}\n\n"
            "Here is the text to convert:\n{text}\n"
            "Respond exclusively with the correctly formatted JSON object, nothing else."
        )}
    ]

    response = openai_client.chat.completions.create(
        model="Aduanas",
        messages=messages,
        max_tokens=4096,
        temperature=0
    )

    if response.choices:
        parsed_json_text = response.choices[0].message.content.strip()
        cleaned_json_text = clean_json_text(parsed_json_text)
        try:
            return json.loads(cleaned_json_text)
        except json.JSONDecodeError as e:
            st.error(f"Error al decodificar el JSON generado: {e}")
            return None
    else:
        st.error("No se obtuvo una respuesta válida del modelo.")
        return None

# Función para procesar los documentos (OCR y conversión a JSON)
def process_documents(uploaded_files, document_type, json_data):
    """Función para procesar múltiples documentos."""
    for uploaded_file in uploaded_files:
        st.write(f"Procesando {document_type}: {uploaded_file.name}")

        # Extraer el texto del archivo usando OCR
        with st.spinner(f"Extrayendo texto de {uploaded_file.name}..."):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            extracted_text = loop.run_until_complete(ocr_with_azure(uploaded_file, cv_client))

        if extracted_text:
            # Cargar la plantilla adecuada
            json_template = get_json_template(document_type)
            if json_template:
                parsed_json = parse_as_json(extracted_text, json_template)
                if parsed_json: 
                    json_data[uploaded_file.name] = parsed_json

# Cargar la plantilla adecuada según el tipo de documento
def get_json_template(document_type):
    """Cargar la plantilla JSON según el tipo de documento."""
    templates_folder = "json_templates"  # Asegúrate de crear esta carpeta
    if document_type == "Bill of Lading":
        template_path = os.path.join(templates_folder, "bill_of_lading.json")
    elif document_type == "Certificado de Origen":
        template_path = os.path.join(templates_folder, "certificate_of_origin.json")
    elif document_type == "Factura":
        template_path = os.path.join(templates_folder, "commercial_invoice.json")
    elif document_type == "Lista de Empaque":
        template_path = os.path.join(templates_folder, "packing_list.json")
    else:
        st.error(f"No se encontró una plantilla para el tipo de documento: {document_type}")
        return None

    # Cargar y devolver la plantilla JSON
    try:
        with open(template_path, 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        st.error(f"Archivo de plantilla no encontrado: {template_path}")
        return None

# Interfaz de Streamlit con opciones de procesamiento
st.title("Comparación de Documentos - Aduanas")

# Carga de múltiples archivos para cada tipo de documento
st.header("Cargar Bill of Lading")
uploaded_bls = st.file_uploader("Sube tus archivos de Bill of Lading (PDF)", type=["pdf"], key="bl", accept_multiple_files=True)

st.header("Cargar Certificados de Origen")
uploaded_cos = st.file_uploader("Sube tus archivos de Certificado de Origen (PDF)", type=["pdf"], key="co", accept_multiple_files=True)

st.header("Cargar Facturas")
uploaded_invoices = st.file_uploader("Sube tus archivos de Factura (PDF)", type=["pdf"], key="invoice", accept_multiple_files=True)

st.header("Cargar Listas de Empaque")
uploaded_packing_lists = st.file_uploader("Sube tus archivos de Lista de Empaque (PDF)", type=["pdf"], key="packing_list", accept_multiple_files=True)

# Botón para iniciar la extracción y procesamiento de OCR
if st.button("Iniciar procesamiento de OCR"):
    json_data = {}

    # Procesar cada grupo de archivos si fueron subidos
    if uploaded_bls:
        process_documents(uploaded_bls, "Bill of Lading", json_data)
    if uploaded_cos:
        process_documents(uploaded_cos, "Certificado de Origen", json_data)
    if uploaded_invoices:
        process_documents(uploaded_invoices, "Factura", json_data)
    if uploaded_packing_lists:
        process_documents(uploaded_packing_lists, "Lista de Empaque", json_data)

    # Mostrar los resultados de los documentos procesados
    if json_data:
        # Crear un archivo .zip con los JSON generados
        zip_filename = "documentos_procesados.zip"
        with zipfile.ZipFile(zip_filename, 'w') as zipf:
            for filename, data in json_data.items():
                json_str = json.dumps(data, indent=4)
                json_path = f"{filename}.json"
                with open(json_path, 'w', encoding='utf-8') as json_file:
                    json_file.write(json_str)
                zipf.write(json_path)
                os.remove(json_path)  # Eliminar el archivo temporal

        # Botón para descargar el archivo .zip generado
        with open(zip_filename, "rb") as f:
            st.download_button(
                label="Descargar JSONs comprimidos",
                data=f,
                file_name=zip_filename,
                mime="application/zip"
            )

        # Eliminar el archivo .zip temporal
        os.remove(zip_filename)
    else:
        st.warning("No se extrajeron datos de los documentos.")

            