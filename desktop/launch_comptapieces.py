"""Native Windows shell for the hosted ComptaPièces service."""

import webview


APP_URL = "https://comptable-327n.onrender.com/"


if __name__ == "__main__":
    webview.create_window(
        "ComptaPièces",
        APP_URL,
        width=1440,
        height=940,
        min_size=(1024, 700),
        text_select=True,
    )
    webview.start(gui="edgechromium", debug=False)
