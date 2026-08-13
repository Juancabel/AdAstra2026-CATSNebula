<#
.SYNOPSIS
    Corrida completa de ingesta: dos ejecuciones, idioma y las 9 validaciones.

.DESCRIPTION
    Lanzador fino sobre scripts/corrida_completa.py, que es donde vive la
    logica. La razon de que el trabajo lo haga Python y no PowerShell esta
    explicada en el docstring de ese archivo; en corto, son dos trampas de
    PowerShell 5.1 en Windows:

      - Tee-Object sobre la salida de un .exe produce mojibake en los acentos,
        porque Python cambia de codificacion al escribir a una tuberia y
        PowerShell decodifica con otra. Start-Transcript tampoco vale: no
        captura la salida de ejecutables nativos.
      - Con $ErrorActionPreference = 'Stop', cualquier linea en stderr de un
        .exe se vuelve NativeCommandError y aborta el script. PyMuPDF escribe
        un aviso de deprecacion nada mas importarse.

    Salida por pantalla en vivo y copia en data/corrida_<marca>.log.
    Dura ~95 min: el OCR de los 51 PDF escaneados corre dos veces, una por
    ejecucion, porque el criterio 9 compara corridas completas.

.PARAMETER SaltarSegundaCorrida
    Omite la segunda ejecucion. Deja el criterio 9 sin comprobar.

.EXAMPLE
    .\scripts\corrida_completa.ps1
    .\scripts\corrida_completa.ps1 -SaltarSegundaCorrida
#>
[CmdletBinding()]
param(
    [switch]$SaltarSegundaCorrida
)

$ErrorActionPreference = 'Stop'

$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz

$Python = Join-Path $Raiz '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "No se encuentra el entorno virtual en $Python. Crealo con: python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt"
}

$Argumentos = @('scripts/corrida_completa.py')
if ($SaltarSegundaCorrida) {
    $Argumentos += '--saltar-segunda-corrida'
}

# SIN tuberia y SIN 2>&1, a proposito: asi Python escribe directo a la consola,
# usa la API Unicode de Windows y los acentos salen bien aunque la consola este
# en una codepage heredada. El log lo escribe Python por su cuenta.
# $ErrorActionPreference baja a Continue para que un aviso en stderr no aborte.
$previo = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $Python -u @Argumentos
$Codigo = $LASTEXITCODE
$ErrorActionPreference = $previo

exit $Codigo
