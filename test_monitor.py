import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
from monitor import abrir_db, carro, ciclo_paginado, km_do_texto, load_config

EXEMPLO = Path(__file__).with_name("config.exemplo.toml")


def yaris(id, preco, ano=2012, km=120000, origem="olx", titulo="Toyota Yaris 1.0 VVT-i", **extra):
    return carro(origem, id, titulo, preco, f"https://exemplo/{id}", ano=ano, km=km, **extra)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.db = abrir_db(":memory:")
        self.addCleanup(self.db.close)
        self.config = load_config(EXEMPLO)
        self.paginas = {}
        patcher = patch.dict(monitor.PAGINADAS, {
            origem: (lambda pagina, origem=origem: iter(self.paginas.get((origem, pagina), [])))
            for origem in ("olx", "standvirtual")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ciclo(self, origem="olx", **paginas):
        self.paginas = {(origem, int(k[1:])): v for k, v in paginas.items()}
        with patch("monitor.send") as send:
            ciclo_paginado(self.db, origem, self.config)
        return [call.args[0] for call in send.call_args_list]

    def test_km_do_texto(self):
        self.assertEqual(km_do_texto("ano 2021 158 mil kms"), 158000)
        self.assertEqual(km_do_texto("158.000 km"), 158000)
        self.assertEqual(km_do_texto("Clio 1.5 dci 2015"), None)

    def test_fluxo_completo(self):
        # 1.º ciclo: referência inicial, 12 Yaris a ~6000 € e nenhum alerta
        mercado = [yaris(f"m{i}", 5800 + i * 40) for i in range(12)]
        self.assertEqual(self.ciclo(p1=mercado), [])

        novos = [
            yaris("barato", 4500),                                   # 25% abaixo → alerta
            yaris("justo", 5900),                                    # na mediana → não
            yaris("pecas", 2500, titulo="Toyota Yaris para peças"),  # excluído
            yaris("caro", 9000),                                     # acima do preco_max
            yaris("km", 4000, km=260000),                            # km a mais
            yaris("burla", 2000),                                    # alerta com aviso
            carro("olx", "outro", "Fiat Punto", 3000, "https://exemplo/outro"),  # sem regra
        ]
        alertas = self.ciclo(p1=novos + mercado[:1])
        self.assertEqual(len(alertas), 2, alertas)
        self.assertIn("4 500 €", alertas[0])
        self.assertIn("abaixo da mediana", alertas[0])
        self.assertIn("⚠️", alertas[1])

        # repetidos não voltam a alertar; descida ≥ 5% volta
        self.assertEqual(self.ciclo(p1=novos), [])
        descida = self.ciclo(p1=[yaris("justo", 4600)])
        self.assertEqual(len(descida), 1)
        self.assertIn("Baixou de 5 900 €", descida[0])

        # o mesmo carro noutro site não duplica o alerta
        self.ciclo("standvirtual", p1=[yaris("ref", 6000, origem="standvirtual")])
        self.assertEqual(self.ciclo("standvirtual", p1=[yaris("sv1", 4500, origem="standvirtual")]), [])

    def test_pagina_ate_encontrar_conhecido(self):
        self.ciclo(p1=[yaris("a", 6000)])
        lidas = []
        original = monitor.PAGINADAS["olx"]
        with patch.dict(monitor.PAGINADAS, {"olx": lambda p: lidas.append(p) or original(p)}):
            self.ciclo(p1=[yaris("b", 6000)], p2=[yaris("c", 6000), yaris("a", 6000)], p3=[yaris("d", 6000)])
        self.assertEqual(lidas, [1, 2])

    def test_telegram_falha_e_repete(self):
        self.ciclo(p1=[yaris(f"m{i}", 6000) for i in range(10)])
        self.paginas = {("olx", 1): [yaris("novo", 4000)]}
        with patch("monitor.send", side_effect=RuntimeError("Telegram em baixo")):
            with self.assertRaises(RuntimeError):
                ciclo_paginado(self.db, "olx", self.config)
        self.assertEqual(len(self.ciclo(p1=[yaris("novo", 4000)])), 1)


if __name__ == "__main__":
    unittest.main()
