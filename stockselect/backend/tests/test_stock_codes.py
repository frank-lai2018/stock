import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "fetch_stock_codes", Path(__file__).resolve().parents[3] / "fetch_stock_codes.py")
stock_codes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock_codes)


class StockCodesTest(unittest.TestCase):
    def test_isin_cp950_names_keep_extension_characters(self):
        html = """<table>
          <tr><td>代號及名稱</td><td>ISIN</td><td>上市日</td><td>市場別</td><td>產業別</td><td>CFICode</td><td>備註</td></tr>
          <tr><td>6285　啟碁</td><td>TW0006285000</td><td>2003/09/22</td><td>上市</td><td>通信網路業</td><td>ESVUFR</td><td></td></tr>
          <tr><td>2353　宏碁</td><td>TW0002353000</td><td>1996/09/18</td><td>上市</td><td>電腦及週邊設備業</td><td>ESVUFR</td><td></td></tr>
          <tr><td>3046　建碁</td><td>TW0003046000</td><td>2002/08/26</td><td>上市</td><td>電腦及週邊設備業</td><td>ESVUFR</td><td></td></tr>
          <tr><td>6690　安碁資訊</td><td>TW0006690001</td><td>2019/11/01</td><td>上櫃</td><td>資訊服務業</td><td>ESVUFR</td><td></td></tr>
          <tr><td>8349　恒耀</td><td>TW0008349009</td><td>2006/03/10</td><td>上櫃</td><td>鋼鐵工業</td><td>ESVUFR</td><td></td></tr>
          <tr><td>2330　台積電</td><td>TW0002330008</td><td>1994/09/05</td><td>上市</td><td>半導體業</td><td>ESVUFR</td><td></td></tr>
        </table>"""
        with patch.object(stock_codes.urllib.request, "urlopen",
                          return_value=io.BytesIO(html.encode("cp950"))):
            rows = stock_codes.fetch_isin(2, "stock").set_index("股票代碼")
        self.assertEqual(rows["中文名稱"].to_dict(),
                         {"6285": "啟碁", "2353": "宏碁", "3046": "建碁", "6690": "安碁資訊",
                          "8349": "恒耀", "2330": "台積電"})
        self.assertEqual(rows.loc["6285", "證券類別"], "stock")

    def test_etf_cp950_names_and_alphanumeric_ids_are_preserved(self):
        html = """<table>
          <tr><td>代號及名稱</td><td>ISIN</td><td>上市日</td><td>市場別</td><td>產業別</td><td>CFICode</td><td>備註</td></tr>
          <tr><td>00665L　富邦恒生國企正2</td><td>TW00000665L6</td><td>2016/08/05</td><td>上市</td><td></td><td>CEOGEU</td><td></td></tr>
        </table>"""
        with patch.object(stock_codes.urllib.request, "urlopen",
                          return_value=io.BytesIO(html.encode("cp950"))):
            rows = stock_codes.fetch_isin(2, "etf").to_dict("records")
        self.assertEqual(rows[0]["股票代碼"], "00665L")
        self.assertEqual(rows[0]["中文名稱"], "富邦恒生國企正2")
        self.assertEqual(rows[0]["證券類別"], "etf")

    def test_invalid_bytes_fail_instead_of_silently_dropping_name_characters(self):
        with patch.object(stock_codes.urllib.request, "urlopen",
                          return_value=io.BytesIO(b"<table>\xff</table>")):
            with self.assertRaises(UnicodeDecodeError):
                stock_codes.fetch_isin(2, "stock")


if __name__ == "__main__":
    unittest.main()
