# Instalación de Tesseract y dependencias Python

1. Instalar el binario de Tesseract (requerido por `pytesseract`):

   - Windows (PowerShell con permisos de Administrador): ejecutar el script
     `scripts/install_tesseract.ps1` si tu sistema tiene Chocolatey configurado.
   - Alternativa manual: descargar el instalador desde
     https://github.com/tesseract-ocr/tesseract/releases y seguir instrucciones.

2. Instalar las dependencias Python del proyecto:

```powershell
python -m pip install -r requirements.txt
```

3. Verificar que Tesseract está disponible en PATH:

```powershell
tesseract --version
```

4. Notas:

- Si trabajas con PDFs escaneados, `PyMuPDF` permite extraer páginas como imagenes
  y luego aplicar OCR con `pytesseract` (flujo híbrido).
- Si necesitas idiomas adicionales, instala los paquetes de idiomas de Tesseract
  (por ejemplo, `spa` para español) o añade la ruta correspondiente a
  `pytesseract.pytesseract.tesseract_cmd`.
