import unittest
from pathlib import Path
from unittest.mock import patch

from alerta import avaliacao as av
from alerta import texto, vigia
from alerta.anuncio import carro
from alerta.config import load_config
from alerta.db import abrir_db, limpar, vale_guardar
from alerta.fontes import PAGINADAS
from alerta.negocio import melhor
from alerta.texto import km_do_texto, modelo_de
from alerta.vigia import ciclo_paginado

EXEMPLO = Path(__file__).with_name("config.exemplo.toml")


def yaris(id, preco, ano=2012, km=120000, origem="olx", titulo="Toyota Yaris 1.0 VVT-i", **extra):
    return carro(origem, id, titulo, preco, f"https://exemplo/{id}", ano=ano, km=km, **extra)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.db = abrir_db(":memory:")
        self.addCleanup(self.db.close)
        self.config = load_config(EXEMPLO)
        self.config["marcas_foco"] = []  # sem pedidos reais aos sites nos testes
        self.paginas = {}
        patcher = patch.dict(PAGINADAS, {
            origem: (lambda pagina, origem=origem: iter(self.paginas.get((origem, pagina), [])))
            for origem in ("olx", "standvirtual")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ciclo(self, origem="olx", **paginas):
        self.paginas = {(origem, int(k[1:])): v for k, v in paginas.items()}
        with patch("alerta.vigia.send") as send:
            ciclo_paginado(self.db, origem, self.config)
        return [call.args[0] for call in send.call_args_list]

    def test_texto(self):
        self.assertEqual(km_do_texto("ano 2021 158 mil kms"), 158000)
        self.assertEqual(km_do_texto("158.000 km"), 158000)
        self.assertEqual(km_do_texto("Clio 1.5 dci 2015"), None)
        self.assertEqual(modelo_de("2015 VW Golf 1.6 TDI"), "volkswagen golf")
        self.assertEqual(modelo_de("Vendo Renault Clio"), "renault clio")

    def test_modelos_agrupados(self):
        self.assertEqual(modelo_de("VW Golf 2.0 TDI R-Line"), "volkswagen golf")
        self.assertEqual(modelo_de("Ford Fiesta 1.0 EcoBoost ST-Line"), "ford fiesta")
        self.assertEqual(modelo_de("VW Golf GTI"), "volkswagen golf gti")
        self.assertEqual(modelo_de("Mercedes-Benz Classe A 180 d AMG Line"), "mercedes-benz a")
        self.assertEqual(modelo_de("Mercedes A 180"), "mercedes-benz a")
        self.assertEqual(modelo_de("BMW 320d Touring"), "bmw serie 3")
        self.assertEqual(modelo_de("BMW Série 3 Touring"), "bmw serie 3")
        self.assertEqual(modelo_de("BMW X3 xDrive"), "bmw x3")

    def test_alertar_todos_nao_duplica(self):
        self.config["alertar_todos"] = True
        self.ciclo(p1=[yaris(f"m{i}", 6000 + i * 10) for i in range(10)])
        self.assertEqual(len(self.ciclo(p1=[yaris("a1", 4000)])), 1)
        self.ciclo("standvirtual", p1=[yaris("ref", 6000, origem="standvirtual")])
        self.assertEqual(self.ciclo("standvirtual", p1=[yaris("a2", 4000, origem="standvirtual")]), [])

    def test_avariados_entram_com_etiqueta(self):
        clio = lambda id, preco, **e: carro("olx", id, "Renault Clio 1.2", preco, f"https://exemplo/{id}",
                                            ano=2012, km=120000, **e)
        self.ciclo(p1=[yaris(f"m{i}", 6000 + i * 10) for i in range(10)]
                   + [clio(f"c{i}", 6000 + i * 10) for i in range(10)])
        alertas = self.ciclo(p1=[
            yaris("embraiagem", 3800, texto="Embraiagem a patinar, de resto impecável. Livro de revisões."),
            yaris("salvado", 3800, texto="Carro salvado de seguradora."),
            clio("clio", 3800, texto="Motor avariado, precisa de motor. Restante em bom estado, inspeção válida."),
        ])
        # avariados de qualquer marca entram, sem custo de reparação; salvado (inviável) não
        self.assertEqual(len(alertas), 2, alertas)
        self.assertTrue(all("AVARIA" in a and "Reparação estimada" not in a for a in alertas))
        self.assertIn("embraiagem a patinar", alertas[0])
        self.assertIn("motor avariado", alertas[1])

    def test_avaria_e_criterio_toyota(self):
        r = av.avaria(av.sinais_do_texto("Yaris", "Avariado, não pega. Embraiagem a patinar. Pneus gastos."))
        self.assertEqual(r["itens"], ["embraiagem a patinar"])  # o genérico só conta se nada for concreto
        r = av.avaria(av.sinais_do_texto("Yaris", "Está avariado."))
        self.assertTrue(r["tem"] and r["incerta"])
        self.assertFalse(av.avaria(av.sinais_do_texto("Yaris", "Pneus gastos, riscos."))["tem"])
        self.assertEqual(av.avaria(av.sinais_do_texto("Yaris", "Carro penhorado"))["inviaveis"], ["penhorado"])
        # a avaria não tira pontos
        mercado = {"desconto": 25, "margem": 2000, "n": 15, "km_mediano": 120000}
        bom = av.pontuar({"km": 120000}, mercado, av.sinais_do_texto("Yaris", ""), {})[0]
        avariado = av.pontuar({"km": 120000}, mercado, av.sinais_do_texto("Yaris", "Motor avariado."), {})[0]
        self.assertEqual(bom, avariado)
        # Toyota (marcas_foco) basta 20% abaixo; as outras marcas precisam de 30%
        analise = {"mediana": 6000, "fiavel": True, "irreal": False, "avaria": {"inviaveis": []},
                   "ratio": 0.8, "n": 12, "pontuacao": 80}
        config = {**self.config, "marcas_foco": ["toyota"]}
        self.assertTrue(melhor({"modelo": "toyota yaris"}, analise, config))
        self.assertFalse(melhor({"modelo": "renault clio"}, analise, config))
        self.assertFalse(melhor({"modelo": "toyota yaris"}, {**analise, "n": 8}, config))
        self.assertEqual(texto.combustivel_do_titulo("BMW 320d Touring"), "Gasóleo")
        self.assertEqual(texto.combustivel_do_titulo("VW Golf 1.0 TSI"), "Gasolina")
        self.assertEqual(texto.cilindrada_do_texto("Renault Clio 1.5 dCi 90cv"), 1500)
        self.assertEqual(texto.potencia_do_texto("Renault Clio 1.5 dCi 90cv"), 90)
        self.assertEqual(texto.km_da_faixa("190 000 - 199 999"), 195000)

    def test_explorar_sobrevive_a_site_mudado(self):
        def partido(pagina, **_):
            raise KeyError("listing")
        avisos = []
        with patch.dict(PAGINADAS, {"olx": partido, "standvirtual": partido, "custojusto": partido}):
            vigia.explorar(self.db, self.config, aviso=avisos.append)
        self.assertEqual(len(avisos), 3)

    def test_regras_da_base(self):
        self.assertFalse(vale_guardar({"modelo": None, "preco": 1000}, self.config))  # trotinete, mota...
        self.assertFalse(vale_guardar({"modelo": "bmw serie 5", "preco": 30000}, self.config))
        self.assertTrue(vale_guardar({"modelo": "toyota yaris", "preco": 6000}, self.config))
        self.ciclo(p1=[yaris("velho", 6000), yaris("novo", 6100), yaris("caro", 30000),
                       carro("olx", "trotinete", "Trotinete elétrica", 200, "https://exemplo/t")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0], 2)  # caro e trotinete fora
        self.db.execute("UPDATE anuncios SET atualizado_em = '2000-01-01' WHERE id = 'velho'")
        self.assertEqual(limpar(self.db, self.config), 1)  # não visto há mais de 60 dias
        self.assertEqual([r[0] for r in self.db.execute("SELECT id FROM anuncios")], ["novo"])

    def test_fluxo_completo(self):
        # 1.º ciclo: referência inicial (mercado do Yaris 2012 ≈ 6000 €), sem alertas
        mercado = [yaris(f"m{i}", 5800 + i * 40) for i in range(12)]
        self.assertEqual(self.ciclo(p1=mercado), [])

        novos = [
            yaris("barato", 4000),                                   # ~34% abaixo → alerta
            yaris("justo", 5900),                                    # no mercado → não
            yaris("pouca", 4600),                                    # ~24% abaixo: < 30% → não
            yaris("pecas", 2500, titulo="Toyota Yaris para peças"),  # excluído
            yaris("km", 4000, km=300000),                            # km a mais
            yaris("burla", 2600),                                    # ~43%: possível burla, pontuação baixa → não
            yaris("irreal", 1500),                                   # ~25%: burla ou erro, ignorado
            carro("olx", "outro", "Fiat Punto", 3000, "https://exemplo/outro", ano=2012),  # sem mercado
        ]
        alertas = self.ciclo(p1=novos + mercado[:1])
        self.assertEqual(len(alertas), 1, alertas)
        self.assertIn("4 000 €", alertas[0])
        self.assertIn("Margem estimada", alertas[0])

        # repetidos não voltam a alertar; descida ≥ 5% volta
        self.assertEqual(self.ciclo(p1=novos), [])
        descida = self.ciclo(p1=[yaris("justo", 4000)])
        self.assertEqual(len(descida), 1)
        self.assertIn("Baixou de 5 900 €", descida[0])

        # o mesmo carro noutro site não duplica o alerta
        self.ciclo("standvirtual", p1=[yaris("ref", 6000, origem="standvirtual")])
        self.assertEqual(self.ciclo("standvirtual", p1=[yaris("sv1", 4000, origem="standvirtual")]), [])

    def test_lista_de_modelos_restringe(self):
        self.ciclo(p1=[yaris(f"m{i}", 6000 + i * 10) for i in range(10)])
        self.config["modelos"] = ["honda jazz"]
        self.assertEqual(self.ciclo(p1=[yaris("novo", 4000)]), [])

    def test_pagina_ate_encontrar_conhecido(self):
        self.ciclo(p1=[yaris("a", 6000)])
        lidas = []
        original = PAGINADAS["olx"]
        with patch.dict(PAGINADAS, {"olx": lambda p: lidas.append(p) or original(p)}):
            self.ciclo(p1=[yaris("b", 6000)], p2=[yaris("c", 6000), yaris("a", 6000)], p3=[yaris("d", 6000)])
        self.assertEqual(lidas, [1, 2])

    def test_telegram_falha_e_repete(self):
        self.ciclo(p1=[yaris(f"m{i}", 6000 + i * 10) for i in range(10)])
        self.paginas = {("olx", 1): [yaris("novo", 4000)]}
        with patch("alerta.vigia.send", side_effect=RuntimeError("Telegram em baixo")):
            with self.assertRaises(RuntimeError):
                ciclo_paginado(self.db, "olx", self.config)
        self.assertEqual(len(self.ciclo(p1=[yaris("novo", 4000)])), 1)


if __name__ == "__main__":
    unittest.main()
