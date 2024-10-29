import streamlit as st
import asyncio
import json
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
from msrest.authentication import CognitiveServicesCredentials
from openai import AzureOpenAI
import os
import requests
import math
import re
from geopy.distance import geodesic
from io import BytesIO
from PIL import Image
from datetime import datetime

# Configurar las credenciales de Azure
AZURE_ENDPOINT = "https://iacdemoaduanas.cognitiveservices.azure.com/"  # Cambia por tu endpoint real
AZURE_KEY = "e44dceb20f40469291dd107c2689e556"  # Cambia por tu API Key real
AZURE_OPENAI_ENDPOINT = "https://iac-demo-aduanas.openai.azure.com/"  # Coloca tu endpoint de Azure OpenAI
AZURE_OPENAI_KEY = "e68adbe619e241f7bb9c9d25389743d2"  # Coloca tu clave de Azure OpenAI
# URL de la API de Static Maps
STATIC_MAP_URL = "https://maps.googleapis.com/maps/api/staticmap"
PLACES_API_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

#Configurar credenciales Google Geocoding
GOOGLE_API_KEY = "AIzaSyAup1kQpy0W1gyaWOY2IoUl9VAHP_7pxYI"
# URL de la API de Geocoding
GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Configurar cliente de Azure Computer Vision
cv_client = ComputerVisionClient(AZURE_ENDPOINT, CognitiveServicesCredentials(AZURE_KEY))

# Configurar cliente de Azure OpenAI
openai_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_KEY,
    api_version="2024-02-01"
)

# Función para normalizar la dirección
def clean_and_normalize_address(address):
    address = re.sub(r'\b(OFICINA|OF|Ofi|OFI|APT|APARTAMENTO|PISO|DEPTO|INTERIOR)\b\s*\d*', '', address, flags=re.IGNORECASE)
    address = re.sub(r'\b(CRA|CRR|CR|CARR)\b', 'Carrera', address, flags=re.IGNORECASE)
    address = re.sub(r'\b(CLL|CL|CALLE)\b', 'Calle', address, flags=re.IGNORECASE)
    address = re.sub(r'\b(DG|DIAG|DIAGONAL)\b', 'Diagonal', address, flags=re.IGNORECASE)
    return address.strip()

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

def obtener_imagen_mapa(latitud, longitud):
    params = {
        'center': f"{latitud},{longitud}",
        'zoom': 17,
        'size': "600x300",
        'markers': f"color:red|label:A|{latitud},{longitud}",
        'maptype': 'satellite',
        'key': GOOGLE_API_KEY
    }
    respuesta = requests.get(STATIC_MAP_URL, params=params)
    return Image.open(BytesIO(respuesta.content))

# Función para obtener lugares cercanos con Google Places
def obtener_lugares_cercanos(latitud, longitud, tipo, radio=50):
    params = {
        'location': f"{latitud},{longitud}",
        'radius': radio,  # Radio de búsqueda en metros
        'type': tipo,
        'key': GOOGLE_API_KEY
    }
    respuesta = requests.get(PLACES_API_URL, params=params)
    if respuesta.status_code == 200:
        datos = respuesta.json()
        return datos.get('results', [])
    return []

# Función para categorizar la zona
def categorizar_zona(latitud, longitud):
    tipos = {
        "residencial": "neighborhood",
        "portuaria": "point_of_interest|establishment",
        "bodegas": "storage"
    }
    radio_busqueda = 201  # Radio en metros
    lugares_residenciales = obtener_lugares_cercanos(latitud, longitud, tipos['residencial'], radio_busqueda)
    lugares_portuarios = obtener_lugares_cercanos(latitud, longitud, tipos['portuaria'], radio_busqueda)
    lugares_bodegas = obtener_lugares_cercanos(latitud, longitud, tipos['bodegas'], radio_busqueda)

    if lugares_residenciales:
        return "Zona residencial"
    elif lugares_portuarios:
        return "Zona portuaria"
    elif lugares_bodegas:
        return "Zona de bodegas"
    else:
        return "Zona desconocida"

def obtener_coordenadas(address):
    """Obtener las coordenadas de una dirección utilizando la API de Google Geocoding."""
    params = {
        'address': address,
        'key': GOOGLE_API_KEY
    }
    respuesta = requests.get(GEOCODING_URL, params=params)
    
    if respuesta.status_code == 200:
        datos = respuesta.json()
        if datos['status'] == 'OK':
            ubicacion = datos['results'][0]['geometry']['location']
            return ubicacion['lat'], ubicacion['lng']
        else:
            st.error(f"No se pudo obtener la ubicación de la dirección: {address}")
            return None, None
    else:
        st.error("Error al conectarse a la API de Google Geocoding")
        return None, None
    
# Función para comparar coordenadas con un umbral de distancia
def comparar_coordenadas(coord1, coord2, umbral_metros=50):
    if None in coord1 or None in coord2:
        return False
    distancia = geodesic(coord1, coord2).meters
    st.write(f"Distancia calculada entre coordenadas: {distancia} metros")  # Mostrar la distancia calculada
    return distancia <= umbral_metros

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

    st.subheader("Datos extraídos:")
    # Inicializamos variables para evitar UnboundLocalError
    invoice_reference_number = None
    packing_list_invoice_number = None
    invoice_date = None
    packing_list_date = None
    streetRUT = None
    streetCC = None
    
    
    for doc_name, doc_data in json_data.items():
        st.write(f"Documento: {doc_name}")
        
        if "bill_of_lading_number" in doc_data:
            st.write(f"Número de B/L: {doc_data.get('bill_of_lading_number', 'No disponible')}")

            consignee = doc_data.get('consignee', {})
            st.write(f"Consignatario: {consignee.get('name', 'No disponible')}")

            transport_details = doc_data.get('transport_details', {})
            st.write(f"Puerto de Carga: {transport_details.get('port_of_loading', 'No disponible')}")
            st.write(f"Puerto de Descarga: {transport_details.get('port_of_discharge', 'No disponible')}")
            
            vessel = doc_data.get('vessel', {})
            st.write(f"Nombre del Buque: {vessel.get('name', 'No disponible')}")
            st.write(f"Número de Viaje: {vessel.get('voyage_number', 'No disponible')}")
        
        elif "commercial_invoice" in doc_data:
            # Mostrar datos de la empresa emisora de la factura
            company = doc_data["commercial_invoice"].get("company", {})
            st.write(f"Empresa emisora: {company.get('name', 'No disponible')}")
            #st.write(f"Dirección de la empresa: {company.get('address', 'No disponible')}")
            #st.write(f"Teléfono de la empresa: {company.get('phone', 'No disponible')}")
            #st.write(f"Fax de la empresa: {company.get('fax', 'No disponible')}")

            # Información del destinatario de la factura
            invoice_to = doc_data["commercial_invoice"].get("invoice_to", {})
            #st.write(f"Factura a nombre de: {invoice_to.get('name', 'No disponible')}")
            addressInv = invoice_to.get("address","No disponible")
            st.write(f"Dirección del destinatario: {invoice_to.get('address', 'No disponible')}")
            #st.write(f"Teléfono del destinatario: {invoice_to.get('phone', 'No disponible')}")

            # Detalles de la factura
            invoice_details = doc_data["commercial_invoice"].get("invoice_details", {})
            invoice_reference_number = invoice_details.get('reference_number', 'No disponible')
            invoice_date = invoice_details.get('invoice_date', 'No disponible')
            st.write(f"Número de referencia de la factura: {invoice_details.get('reference_number', 'No disponible')}")
            st.write(f"Fecha de la factura: {invoice_details.get('invoice_date', 'No disponible')}")
            #st.write(f"Número de Bill of Lading: {invoice_details.get('bill_of_lading_no', 'No disponible')}")
            #st.write(f"Carrier: {invoice_details.get('carrier', 'No disponible')}")

            # País de origen y destino
            #st.write(f"País de exportación: {invoice_details.get('country_of_export', 'No disponible')}")
            #st.write(f"País de origen de los bienes: {invoice_details.get('country_of_origin_of_goods', 'No disponible')}")
            st.write(f"País de destino final: {invoice_details.get('country_of_ultimate_destination', 'No disponible')}")

            # Detalles de los bienes
            goods = doc_data["commercial_invoice"].get("goods", [])
            if goods:
                st.write("Detalles de los bienes:")
                for good in goods[:6]:  # Mostrar solo los primeros 6 bienes
                    st.write(f"- Producto: {good.get('line_item_number', 'No disponible')}")
                    st.write(f"  Número de producto: {good.get('product_number', 'No disponible')}")
                    st.write(f"  Descripción: {good.get('description', 'No disponible')}")
                    st.write(f"  Cantidad: {good.get('quantity', 'No disponible')}")
                    st.write(f"  Valor unitario: {good.get('unit_value', 'No disponible')}")
                    st.write(f"  Valor total: {good.get('total_value', 'No disponible')}")
                    #st.write(f"  Código arancelario: {good.get('harmonized_code', 'No disponible')}")
                    st.write(f"  País de origen: {good.get('country_of_origin', 'No disponible')}")
                    st.write(f"  Número de lote: {good.get('batch_number', 'No disponible')}")
                if len(goods) > 6:
                    st.write(f"... y {len(goods) - 6} artículos más.")

            # Totales
            totals = doc_data["commercial_invoice"].get("totals", {})
            st.write(f"Gran total: {totals.get('grand_total', 'No disponible')}")
            #st.write(f"Total de paquetes: {totals.get('total_number_of_packages', 'No disponible')}")
            #st.write(f"Peso total: {totals.get('total_weight', 'No disponible')}")
            
        elif "packing_list" in doc_data:
            exporter = doc_data["packing_list"].get("exporter", {})
            st.write(f"Empresa exportadora: {exporter.get('name', 'No disponible')}")
            st.write(f"Dirección entrega: {exporter.get('address', 'No disponible')}")
            # Información del comprador
            #buyer = doc_data["packing_list"].get("buyer", {})
            #st.write(f"Comprador: {buyer.get('name', 'No disponible')}")
            #st.write(f"Dirección: {buyer.get('address', 'No disponible')}")
            invoice_details = doc_data["packing_list"].get("invoice_details", {})
            packing_list_invoice_number = invoice_details.get('export_invoice_number', 'No disponible')
            packing_list_date = invoice_details.get('date', 'No disponible')
            st.write(f"Número de factura relacionada: {invoice_details.get('export_invoice_number', 'No disponible')}")
            st.write(f"Fecha: {invoice_details.get('date', 'No disponible')}")
            #
            shipment_details = doc_data["packing_list"].get("shipment_details", {})
            #st.write(f"Ciudad de destino: {shipment_details.get('country_of_final_destination', 'No disponible')}")
            
        elif "RUT" in doc_data:
           NIT = doc_data["RUT"].get("NIT", {})
           st.write(f"Número de NIT: {NIT.get('number', 'No disponible')}")
           address = doc_data["RUT"].get("address", {})
           primary_address = address.get("primary", {})
           streetRUT = primary_address.get("street", "No disponible")
           st.write(f"Dirección RUT: {streetRUT}") 
           
        elif "Camara_de_Comercio" in doc_data:
            company_name = doc_data.get("Camara_de_Comercio", {}).get("company_name", "No disónible")
            # Acceder a la dirección (street) dentro de "Camara_de_Comercio" -> "address"
            address = doc_data.get("Camara_de_Comercio", {}).get("address", {})
            streetCC = address.get("street", "No disponible")
            
            # Mostrar los valores
            st.write(f"Nombre de la empresa: {company_name}")
            st.write(f"Dirección Cámara de Comercio: {streetCC}")            
       
            
    #Si las direcciones de la factura y lista de empaque existen, proceder con la geocodificación y comparación
    #-------------------Está por hacerse-----------------------#
    ##if addressInv and streetLista_Empaque:
        # Normalizar direcciones
        ##direccion_Inv_normalizada = clean_and_normalize_address(addressInv)
        ##direccion_ListaEmpaque_normalizada = clean_and_normalize_address(streetLista_empaque)
        #Obtener coordenadas
        ##coordenadas_Invoice = obtener_coordenadas(direccion_Inv_normalizada)
        ##coordenadas_ListaEmpaque = obtener_coordenadas(direccion_ListaEmpaque_normalizada)
        #Mostrar coordenadas en pantalla
        ##st.write(f"Coordenadas de la dirección de factura: {coordenadas_Invoice}")
        ##st.write(f"Coordenadas de la dirección RUT: {coordenadas_ListaEmpaque}")
        ##if coordenadas_Invoice and coordenadas_ListaEmpaque:
            ##if comparar_coordenadas(coordenadas_Invoice, coordenadas_ListaEmpaque, umbral_metros=50):
                ##st.success("Las direcciones están dentro del margen de 50 metros.")
            ##else:
                ##st.error("Las direcciones están a más de 50 metros de distancia.")
        ##else:
            ##st.error("No se pudo obtener las coordenadas de una o ambas direcciones.")
        
    #Si las direcciones de BL y "", proceder con la geocodificación y comparación
            
    #comparación de números de factura y fecha
    if invoice_reference_number and packing_list_invoice_number and invoice_date and packing_list_date:
        resultado_comparacion_fechas = comparar_fechas(invoice_date, packing_list_date)
        st.write(resultado_comparacion_fechas)  # Mostrar resultado de la comparación de fechas
    
        if invoice_reference_number == packing_list_invoice_number and "coinciden" in resultado_comparacion_fechas:
                st.success("Todos los campos coinciden")
        elif invoice_reference_number == packing_list_invoice_number and "no coinciden" in resultado_comparacion_fechas:
            st.success(f"La fecha '{invoice_date}' **no coincide** con la fecha en la lista de empaque '{packing_list_date}'.")
        elif invoice_reference_number != packing_list_invoice_number and "coinciden" in resultado_comparacion_fechas:
            st.success(f"El número de factura '{invoice_reference_number}' **no coincide** con el número en la lista de empaque '{packing_list_invoice_number}'")
        else:
            st.error("Ninguno de los campos coincide")
    
    # Si ambas direcciones existen, proceder con la geocodificación y comparación
    if streetRUT and streetCC:
        # Normalizar direcciones
        direccion_rut_normalizada = clean_and_normalize_address(streetRUT)
        direccion_cc_normalizada = clean_and_normalize_address(streetCC)
        #Obtener coordenadas
        coordenadas_rut = obtener_coordenadas(direccion_rut_normalizada)
        coordenadas_cc = obtener_coordenadas(direccion_cc_normalizada)
        #Mostrar coordenadas en pantalla
        st.write(f"Coordenadas de la dirección RUT: {coordenadas_rut}")
        st.write(f"Coordenadas de la dirección relacionada con la Cámara de Comercio: {coordenadas_cc}")
        if coordenadas_rut and coordenadas_cc:
            if comparar_coordenadas(coordenadas_rut, coordenadas_cc, umbral_metros=50):
                st.success("Las direcciones están dentro del margen establecido.")
            else:
                st.error("Las direcciones están a más de 50 metros de distancia.")
        else:
            st.error("No se pudo obtener las coordenadas de una o ambas direcciones.")
            
        #Almacenar la dirección normalizada
        st.session_state.direccion_rut_normalizada = direccion_rut_normalizada
    
# Función para procesar los documentos (OCR y conversión a JSON)
def process_document(uploaded_file, document_type, json_data):
    """Función para procesar un documento específico."""
    if uploaded_file:
        st.write(f"Procesando {document_type}: {uploaded_file.name}")

        # Extraer el texto del archivo usando OCR
        with st.spinner(f"Extrayendo texto de {uploaded_file.name}..."):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            extracted_text = loop.run_until_complete(ocr_with_azure(uploaded_file, cv_client))

        if extracted_text:
            #st.write(f"Texto extraído de {uploaded_file.name}:")
            st.text(extracted_text)

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
st.title("Comparación de Documentos - Aduanas")

# Usar Radio Buttons para opciones
selected_option = st.radio(
    "Selecciona una opción:",
    ("Comparación y categorización de direcciones", "Comparación de Documentos")
)

# Si se selecciona "Comparación de Documentos", mostrar campos para cargar documentos
if selected_option == "Comparación y categorización de direcciones":
    #Carga de RUT
    st.header("Cargar RUT")
    uploaded_rut = st.file_uploader("Sube tu archivo de RUT (PDF)", type=["pdf"], key="rut")
    
    # Carga de Cámara de Comercio
    st.header("Cargar Cámara de Comercio")
    uploaded_cc = st.file_uploader("Sube tu archivo de Cámara de comercio (PDF)", type=["pdf"], key="cc")
    
    # Carga de Cotización
    #st.header("Cargar Cotización")
    #uploaded_cot = st.file_uploader("Sube tu archivo de Cotización (PDF)", type=["pdf"], key="cot")
    
    # Botón para iniciar la extracción y procesamiento de OCR
    if st.button("Iniciar Comparación"):
        json_data = {}
        
        # Procesar el archivo RUT si fue subido
        if uploaded_rut:
            process_document(uploaded_rut, "RUT", json_data)
        
        # Procesar el archivo de Cámara de Comercio si fue subido
        if uploaded_cc:
            process_document(uploaded_cc, "Cámara de Comercio", json_data)
        
        # Procesar el archivo de Cotización si fue subido
        #if uploaded_cot:
            #process_document(uploaded_cot, "Cotización", json_data)
        
        # Mostrar los resultados de los documentos procesados
        if json_data:
            st.write("Datos JSON extraídos de los documentos:")
            display_extracted_data(json_data)
            
            
        else:
            st.warning("No se extrajeron datos de los documentos.")
        
    #Si las direcciones ya han sido extraidas y guardadas en session_state
    if "direccion_rut_normalizada" in st.session_state:
        st.subheader("Categorizar Dirección")
        
        # Mostrar la dirección extraída y permitir editarla
        direccion_editada = st.text_input("Edita la dirección para categorizar", value=st.session_state.direccion_rut_normalizada)
    
        # Botón para confirmar la categorización
        if st.button("Categorizar Dirección"):
            direccion_editada_normalizada = clean_and_normalize_address(direccion_editada)
    
            # Obtener coordenadas
            lat_rut, lng_rut = obtener_coordenadas(direccion_editada_normalizada)
            if lat_rut and lng_rut:
                st.write(f"Coordenadas: {lat_rut}, {lng_rut}")
    
                # Obtener imagen del mapa
                imagen_mapa = obtener_imagen_mapa(lat_rut, lng_rut)
                st.image(imagen_mapa, caption="Vista de la ubicación RUT", use_column_width=True)
    
                # Categorizar la zona
                categoria = categorizar_zona(lat_rut, lng_rut)
                st.write(f"Categoría de la zona: {categoria}")
            else:
                st.error("No se pudieron obtener las coordenadas de la dirección.")
    
elif selected_option == "Comparación de Documentos":
    # Carga de Bill of Lading
    st.header("Cargar Bill of Lading")
    uploaded_bl = st.file_uploader("Sube tu archivo de Bill of Lading (PDF)", type=["pdf"], key="bl")

    # Carga de Certificado de Origen
    st.header("Cargar Certificado de Origen")
    uploaded_co = st.file_uploader("Sube tu archivo de Certificado de Origen (PDF)", type=["pdf"], key="co")

    # Carga de Factura (Commercial Invoice)
    st.header("Cargar Factura")
    uploaded_invoice = st.file_uploader("Sube tu archivo de Factura (PDF)", type=["pdf"], key="invoice")

    # Carga de Lista de Empaque (Packing List)
    st.header("Cargar Lista de Empaque")
    uploaded_packing_list = st.file_uploader("Sube tu archivo de Lista de Empaque (PDF)", type=["pdf"], key="packing_list")

    # Botón para iniciar la extracción y procesamiento de OCR
    if st.button("Iniciar procesamiento de OCR"):
        json_data = {}

        # Procesar cada archivo si fue subido
        process_document(uploaded_bl, "Bill of Lading", json_data)
        process_document(uploaded_co, "Certificado de Origen", json_data)
        process_document(uploaded_invoice, "Factura", json_data)
        process_document(uploaded_packing_list, "Lista de Empaque", json_data)

        # Mostrar los resultados de los documentos procesados
        if json_data:
            st.write("Datos JSON extraídos de los documentos:")
            display_extracted_data(json_data)
            # Mostrar el JSON completo
            st.subheader("JSON completo generado:")
            json_str = json.dumps(json_data, indent=4)
            st.text_area("JSON Generado:", json_str, height=300)

            # Botón para descargar el JSON generado
            st.download_button(
                label="Descargar JSON",
                data=json_str,
                file_name="documentos_procesados.json",
                mime="application/json"
            )
        else:
            st.warning("No se extrajeron datos de los documentos.")
            
            
#-------------------------------------------#            
#if coordenadas_rut:
    #st.subheader("Categorización de la Dirección")
    #categoria = categorizar_zona(coordenadas_rut[0], coordenadas_rut[1])
    #st.write(f"Categoría: {categoria}")
    
    # Obtener la imagen del mapa
    #imagen_mapa = obtener_imagen_mapa(coordenadas_rut[0], coordenadas_rut[1])
    #st.image(imagen_mapa, caption="Ubicación de la dirección RUT", use_column_width=True)