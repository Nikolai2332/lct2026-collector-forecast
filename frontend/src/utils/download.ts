/** Сохранить ответ API (XLSX, XML) файлом с именем из Content-Disposition */
export async function saveResponse(res: Response, fallback: string) {
  const cd = res.headers.get('Content-Disposition') ?? '';
  const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(cd);
  const name = m ? decodeURIComponent(m[1] ?? m[2]) : fallback;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
