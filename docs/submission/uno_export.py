"""LibreOffice (UNO, headless): открыть .docx, обновить оглавление и поля, сохранить .docx с заполненным
оглавлением и выгрузить .pdf со встроенными шрифтами."""

import os
import subprocess
import sys
import time

import uno
from com.sun.star.beans import PropertyValue

src, pdf = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


proc = subprocess.Popen(["soffice", "--headless", "--invisible", "--nologo", "--norestore",
                         "--accept=socket,host=127.0.0.1,port=2002;urp;"])
local = uno.getComponentContext()
resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
ctx = None
for _ in range(90):
    try:
        ctx = resolver.resolve("uno:socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext")
        break
    except Exception:
        time.sleep(1)
if ctx is None:
    sys.exit("LibreOffice не запустился")
desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(src), "_blank", 0, (prop("Hidden", True),))
idx = doc.getDocumentIndexes()
for _ in range(2):  # второй проход — номера страниц после того, как оглавление заняло своё место
    for i in range(idx.getCount()):
        idx.getByIndex(i).update()
    doc.getTextFields().refresh()
doc.storeToURL(uno.systemPathToFileUrl(src), (prop("FilterName", "MS Word 2007 XML"),))
filter_data = uno.Any("[]com.sun.star.beans.PropertyValue",
                      (prop("EmbedStandardFonts", True), prop("ExportBookmarks", True)))
doc.storeToURL(uno.systemPathToFileUrl(pdf), (prop("FilterName", "writer_pdf_Export"), prop("FilterData", filter_data)))
n = idx.getCount()
doc.close(True)
print(f"оглавлений обновлено: {n}; PDF: {pdf}")
try:
    desktop.terminate()
except Exception:
    pass
try:
    proc.wait(timeout=30)
except subprocess.TimeoutExpired:
    proc.kill()
