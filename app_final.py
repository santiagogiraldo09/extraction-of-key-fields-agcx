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
AZURE_ENDPOINT = "https://iacdemoaduanas.cognitiveservices.azure.com/"  # Cambia por tu endpoint real
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

# Función para comparar fechas
def comparar_fechas(fecha_factura, fecha_empaque):
    fecha_normalizada_factura = normalizar_fecha(fecha_factura)
    fecha_normalizada_empaque = normalizar_fecha(fecha_empaque)

    if fecha_normalizada_factura and fecha_normalizada_empaque:
        if fecha_normalizada_factura == fecha_normalizada_empaque:
            return "Las fechas coinciden."
        else:
            return f"Las fechas NO coinciden: Factura ({fecha_factura}) vs. Empaque ({fecha_empaque})"
    else:
        return "Una o ambas fechas son inválidas."


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
        st.write(f"Total de páginas procesadas: {len(read_result.analyze_result.read_results)}")
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
            "The JSON object must strictly adhere to this structure, including all keys and nested elements, even if the data in the text is incomplete. "
            "For the 'goods' field, ensure that every item is represented, and include any relevant details such as product number, description, quantity, unit price, total price, country of origin, and batch number. "
            "When interpreting quantities and prices, be aware that a format such as '1.000' may represent one unit, and should not be confused with '1,000.0'. "
            "When you find values ​​in miles in the total value of an item you must be careful, many of these values ​​do not actually represent miles but hundreds, this is because there are companies that mix ',' and '.' without taking into account that they represent quantities such as 1.0 and not 1,000.0. For example the value '73,150.00', you must enter '73.150'"
            "You must count the length of each field that you are going to add, If fields like 'terms_conditions' or 'additional_clauses' are longer than 300 characters, you should put this message instead of all of its characters: 'This section was cut due to its length. See the original document for the full text.'"
            "Additionally, make sure to extract the total document value and fill in the 'grand_total' field. Look for keywords like 'Total Amount', 'Grand Total', 'Total Due', or other similar terms that indicate the total value of the document."
            #"Where you find this value '73,150.00' put '73.150'"
            "Use contextual information from the document to ensure quantities are accurately interpreted.\n"
            f"Here is the text to convert:\n{text}\n"
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

# Función para mostrar datos específicos del JSON
def display_extracted_data(json_data):
    """Función para mostrar datos extraídos del JSON."""
    if not json_data:
        st.error("No hay datos JSON para mostrar.")
        return
    
# Función para procesar los documentos (OCR y conversión a JSON)
#def process_document(uploaded_file, document_type, json_data):
    #"""Función para procesar un documento específico."""
    #if uploaded_file:
        #st.write(f"Procesando {document_type}: {uploaded_file.name}")

        # Extraer el texto del archivo usando OCR
        #with st.spinner(f"Extrayendo texto de {uploaded_file.name}..."):
            #loop = asyncio.new_event_loop()
            #asyncio.set_event_loop(loop)
            #extracted_text = loop.run_until_complete(ocr_with_azure(uploaded_file, cv_client))

        #if extracted_text:
            #st.write(f"Texto extraído de {uploaded_file.name}:")
            #st.text(extracted_text)

            # Cargar la plantilla adecuada
            #json_template = get_json_template(document_type)
            #if json_template:
                #parsed_json = parse_as_json(extracted_text, json_template)
                #if parsed_json: 
                    #json_data[uploaded_file.name] = parsed_json

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
    elif document_type == "RUT":
        template_path = os.path.join(templates_folder, "RUT.json")
    elif document_type == "Cámara de Comercio":
        template_path = os.path.join(templates_folder, "camara_comercio.json")
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
st.title("Comparación de Documentos - Aduanas!!")

# Carga de Bill of Lading
st.header("Cargar Bill of Lading")
uploaded_bl = st.file_uploader("Sube tu archivo de Bill of Lading (PDF)", type=["pdf"], key="bl", accept_multiple_files=True)

# Carga de Certificado de Origen
st.header("Cargar Certificado de Origen")
uploaded_co = st.file_uploader("Sube tu archivo de Certificado de Origen (PDF)", type=["pdf"], key="co", accept_multiple_files=True)

# Carga de Factura (Commercial Invoice)
st.header("Cargar Factura")
uploaded_invoice = st.file_uploader("Sube tu archivo de Factura (PDF)", type=["pdf"], key="invoice", accept_multiple_files=True)

# Carga de Lista de Empaque (Packing List)
st.header("Cargar Lista de Empaque")
uploaded_packing_list = st.file_uploader("Sube tu archivo de Lista de Empaque (PDF)", type=["pdf"], key="packing_list", accept_multiple_files=True)

# Botón para iniciar la extracción y procesamiento de OCR
if st.button("Iniciar procesamiento de OCR"):
    json_data = {}

    # Procesar cada archivo si fue subido
    process_documents(uploaded_bl, "Bill of Lading", json_data)
    process_documents(uploaded_co, "Certificado de Origen", json_data)
    process_documents(uploaded_invoice, "Factura", json_data)
    process_documents(uploaded_packing_list, "Lista de Empaque", json_data)

    # Mostrar los resultados de los documentos procesados
    if json_data:
        #st.write("Datos JSON extraídos de los documentos:")
        display_extracted_data(json_data)
        # Mostrar el JSON completo
        st.subheader("JSON generado:")
        json_str = json.dumps(json_data, indent=4)
        st.text_area("JSON:", json_str, height=300)
        
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

        # Botón para descargar el JSON generado
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
        #st.download_button(
            #label="Descargar JSON",
            #data=json_str,
            #file_name="documentos_procesados.json",
            #mime="application/json"
        #)
    else:
        st.warning("No se extrajeron datos de los documentos.")
            