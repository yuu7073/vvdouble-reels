// VV 實穿草稿產生器 — 待製作清單
// 貼在「2026上架排程」試算表的 擴充功能 → Apps Script，部署為網頁應用程式（執行身分：我；存取：知道連結的任何人）
// 只回傳影片製作需要的欄位：順序、狀態、貨號、款號、名稱、顏色、規格、拍攝特點、模特著用尺碼

function doGet(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var want = (e && e.parameter && e.parameter.tab) || "";
  var out = { updated: new Date().toISOString(), tabs: [] };
  var thisYear = new Date().getFullYear();
  ss.getSheets().forEach(function (sh) {
    var title = sh.getName();
    if (/清點/.test(title)) return;
    // 分頁名稱：2026-1117、💰2026-1103、🎀2026-0602(GA聯名)，或只有 1208 這種月日
    var year = null, code = null;
    var m = title.match(/^[^0-9]*(\d{4})-(\d{4})/);
    if (m) { year = parseInt(m[1], 10); code = m[2]; }
    else {
      m = title.match(/^[^0-9]*(\d{4})(?![\d-])/);
      if (m) { year = thisYear; code = m[1]; }
    }
    if (!code) return;
    var mm = parseInt(code.slice(0, 2), 10), dd = parseInt(code.slice(2), 10);
    if (mm < 1 || mm > 12 || dd < 1 || dd > 31) return;   // 2023門市獨賣 這類不是檔期
    if (year < thisYear - 1) return;                        // 只給最近兩年
    if (want && want !== code) return;
    var vals = sh.getDataRange().getValues();
    if (vals.length < 2) return;
    var head = vals[0].map(function (h) { return String(h || "").replace(/\s+/g, ""); });
    var col = function (names) {
      for (var i = 0; i < names.length; i++) { var k = head.indexOf(names[i]); if (k >= 0) return k; }
      return -1;
    };
    var cOrder = col(["SL呈現順序", "呈現順序", "順序"]);
    var cStatus = col(["狀態"]);
    var cSku = col(["貨號"]);
    var cStyle = col(["款號"]);
    var cName = col(["名稱", "商品名稱"]);
    var cColor = col(["顏色"]);
    var cSpec = col(["規格", "尺寸"]);
    var cFeat = col(["拍攝特點"]);
    var cSize = col(["尺寸表"]);
    var cCat = col(["產品大類"]);
    if (cName < 0) return;
    var items = [];
    for (var r = 1; r < vals.length; r++) {
      var row = vals[r];
      var name = String(row[cName] || "").trim();
      if (!name) continue;
      var lines = function (c) {
        return c < 0 ? [] : String(row[c] || "").split(/\r?\n/).map(function (x) { return x.trim(); }).filter(Boolean);
      };
      // 尺寸表裡的「Model-VV 154cm/92kg   著 3L4L 舒適」→ 每位模特的著用尺碼與合身度
      var models = [];
      if (cSize >= 0) {
        var re = /Model-\s*([^\s]+)\s+(\d+)cm\/(\d+)kg\s+(?:著\s*([^\s]+)\s+)?([^\s]+)/g, mm;
        var txt = String(row[cSize] || "");
        while ((mm = re.exec(txt)) !== null) {
          models.push({ name: mm[1] === "VV" ? "VVN" : mm[1], height: mm[2], weight: mm[3], size: mm[4] || "", fit: mm[5] || "" });
        }
      }
      items.push({
        order: cOrder < 0 ? "" : String(row[cOrder] || ""),
        status: cStatus < 0 ? "" : String(row[cStatus] || "").trim(),
        sku: cSku < 0 ? "" : String(row[cSku] || "").trim(),
        style: cStyle < 0 ? "" : String(row[cStyle] || "").trim(),
        name: name,
        colors: lines(cColor),
        specs: lines(cSpec),
        features: lines(cFeat).filter(function (x) { return !/^x$/i.test(x); }),
        category: cCat < 0 ? "" : String(row[cCat] || "").trim(),
        models: models
      });
    }
    out.tabs.push({ tab: title, year: year, code: code, month: parseInt(code.slice(0, 2), 10), items: items });
  });
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}
