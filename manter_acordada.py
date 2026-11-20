"""Visita a app no Streamlit Cloud para não adormecer; se estiver a dormir, acorda-a."""

import time

from selenium import webdriver
from selenium.webdriver.common.by import By

URL = "https://alerta-carros.streamlit.app/"

opcoes = webdriver.ChromeOptions()
for argumento in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
    opcoes.add_argument(argumento)

with webdriver.Chrome(options=opcoes) as browser:
    browser.get(URL)
    time.sleep(10)
    acordar = browser.find_elements(By.XPATH, "//button[contains(., 'get this app back up')]")
    if acordar:
        acordar[0].click()
        time.sleep(15)
        print("A app estava a dormir: acordada.")
    else:
        print("A app está acordada.")
