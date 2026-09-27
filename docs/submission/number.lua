-- Нумерация рисунков и таблиц в подписях: «Рисунок N — …», «Таблица N — …» (pandoc 2.17)
local fig, tab = 0, 0
function Image(img)
  if #img.caption > 0 then
    fig = fig + 1
    table.insert(img.caption, 1, pandoc.Str("Рисунок " .. fig .. " — "))
  end
  return img
end
function Table(t)
  if t.caption and t.caption.long and #t.caption.long > 0 then
    tab = tab + 1
    local blk = t.caption.long[1]
    table.insert(blk.content, 1, pandoc.Str("Таблица " .. tab .. " — "))
  end
  return t
end
