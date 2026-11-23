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
from alerta.catalogo import Catalogo, identificar
from alerta.texto import km_do_texto
from alerta.vigia import ciclo_paginado

EXEMPLO = Path(__file__).with_name("config.exemplo.toml")


def yaris(id, preco, ano=2012, km=120000, origem="olx", titulo="Toyota Yaris 1.0 VVT-i", **extra):
    # como no Standvirtual/OLX: marca e modelo vêm em campos do site
    extra = {"marca": "toyota", "modelo_site": "yaris", **extra}
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
            for origem in ("olx", "standvirtual", "custojusto", "autopt", "autox")})
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

    def test_marca_e_modelo_sem_adivinhar(self):
        # campos do site (Standvirtual/OLX) são a referência
        a = identificar(self.db, carro("standvirtual", "1", "Qualquer título", 5000, "u",
                                       marca="mercedes-benz", modelo_site="c-220"))
        self.assertEqual((a["modelo"], a["modelo_fonte"]), ("mercedes-benz c-220", "site"))
        # os outros sites só com correspondência exata ao catálogo aprendido desses campos
        cat = Catalogo(["toyota yaris", "toyota yaris-cross", "bmw 320", "mercedes-benz c-220", "citroen c3",
                        "citroen c30", "land-rover range-rover-evoque"])
        def cj(titulo, **e):
            return identificar(self.db, carro("custojusto", "x", titulo, 5000, "u", **e), cat)["modelo"]
        self.assertEqual(cj("Toyota Yaris 1.0 VVT-i"), "toyota yaris")
        self.assertEqual(cj("Toyota Yaris Cross Hybrid"), "toyota yaris-cross")  # o mais específico
        self.assertEqual(cj("BMW 320d Touring"), "bmw 320")  # "320d" = 320 diesel
        self.assertEqual(cj("Mercedes C 220 CDI"), "mercedes-benz c-220")  # "mercedes" só pode ser uma marca
        self.assertEqual(cj("Citroen C3 1.1"), "citroen c3")  # e não "c30"
        self.assertEqual(cj("Land Rover Range Rover Evoque"), "land-rover range-rover-evoque")
        self.assertIsNone(cj("VW Golf 1.6 TDI"))  # nem marca nem modelo no catálogo: não se adivinha
        self.assertIsNone(cj("Toyota Corola 1.4"))  # erro de escrita: não se adivinha
        self.assertIsNone(cj("Trotinete elétrica Xiaomi"))
        self.assertEqual(cj("Yaris 1.0", marca="Toyota"), "toyota yaris")  # marca dada pelo site (AutoX, Auto.pt)
        # abreviaturas aprendidas dos títulos em que o site diz a marca
        titulos = [("volkswagen", f"VW Golf {i}") for i in range(6)] + [("volkswagen", "Volkswagen Polo")]
        titulos += [(m, "Vendo carro") for m in ("toyota", "bmw", "citroen", "volkswagen", "kia", "opel")]
        cat = Catalogo(["volkswagen golf", "volkswagen jetta", "toyota yaris"], titulos)
        self.assertEqual(cat.abreviaturas, {"vw": "volkswagen"})  # "vendo" aparece com várias marcas: não
        self.assertEqual(cj("VW Jetta 1.9 TDI"), "volkswagen jetta")
        poucos = Catalogo(["volkswagen jetta"], [("volkswagen", "VW Golf")] * 4)  # menos de 5: não aprende
        self.assertEqual(poucos.abreviaturas, {})

    def test_alertar_todos_nao_duplica(self):
        self.config["alertar_todos"] = True
        self.ciclo(p1=[yaris(f"m{i}", 6000 + i * 10) for i in range(10)])
        self.assertEqual(len(self.ciclo(p1=[yaris("a1", 4000)])), 1)
        self.ciclo("standvirtual", p1=[yaris("ref", 6000, origem="standvirtual")])
        self.assertEqual(self.ciclo("standvirtual", p1=[yaris("a2", 4000, origem="standvirtual")]), [])

    def test_telegram_so_da_marca_em_foco(self):
        clio = lambda id, preco: carro("olx", id, "Renault Clio 1.2", preco, f"https://exemplo/{id}",
                                       ano=2012, km=120000, marca="renault", modelo_site="clio")
        self.config["marcas_foco"] = ["toyota"]
        self.ciclo(p1=[yaris(f"m{i}", 6000 + i * 10) for i in range(10)]
                   + [clio(f"c{i}", 6000 + i * 10) for i in range(10)])
        alertas = self.ciclo(p1=[clio("clio", 3800), yaris("yaris", 3800)])
        self.assertEqual(len(alertas), 1, alertas)  # só o Toyota; o Clio fica só no painel
        self.assertIn("Yaris", alertas[0])
        self.config["so_foco_telegram"] = False  # e com o filtro desligado o Clio também avisa
        self.assertEqual(len(self.ciclo(p1=[clio("clio2", 3800)])), 1)

    def test_verificar_marca_eliminados_e_painel_esconde(self):
        from alerta.vigia import verificar
        self.ciclo(p1=[yaris("vivo", 4000), yaris("morto", 4100), yaris("duvida", 4200)])
        respostas = {"https://exemplo/vivo": True, "https://exemplo/morto": False, "https://exemplo/duvida": None}
        with patch("alerta.vigia.ainda_existe", side_effect=lambda url: respostas[url]):
            self.assertEqual(verificar(self.db, self.config), 1)
        estado = dict(self.db.execute("SELECT id, removido FROM anuncios"))
        self.assertEqual(estado, {"vivo": 0, "morto": 1, "duvida": None})  # dúvida: fica por verificar
        with patch("alerta.vigia.ainda_existe") as outra:  # já verificados há pouco: não volta a abrir os sites
            verificar(self.db, self.config)
        self.assertEqual(outra.call_count, 1)  # só o que ficou em dúvida

    def test_avariados_entram_com_etiqueta(self):
        self.config["so_foco_telegram"] = False  # (todas as marcas: o filtro Toyota tem teste próprio)
        clio = lambda id, preco, **e: carro("olx", id, "Renault Clio 1.2", preco, f"https://exemplo/{id}",
                                            ano=2012, km=120000, marca="renault", modelo_site="clio", **e)
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

    def test_todos_os_sites_chegam_ao_telegram(self):
        # mercado do Yaris com marca/modelo do site (Standvirtual), como na realidade
        self.ciclo("standvirtual", p1=[yaris(f"m{i}", 6000 + i * 10, origem="standvirtual") for i in range(12)])
        for origem, extra in (("standvirtual", {}), ("olx", {}),
                              ("custojusto", {"marca": None, "modelo_site": None}),  # só título: catálogo
                              ("autopt", {"marca": "Toyota", "modelo_site": "Yaris"}),
                              ("autox", {"marca": "Toyota", "modelo_site": "Yaris 1.0 VVT-i"})):
            self.ciclo(origem, p1=[yaris(f"{origem}-ref", 6000, origem=origem, **extra)])  # 1.ª leitura = referência
            barato = yaris(f"{origem}-barato", 3900, origem=origem, km=119000 + len(origem), **extra)
            with patch("alerta.fontes.detalhe", side_effect=lambda a: a),                  patch("alerta.vigia.detalhe", side_effect=lambda a: a):  # sem pedidos reais
                alertas = self.ciclo(origem, p1=[barato])
            self.assertEqual(len(alertas), 1, f"{origem}: {alertas}")
        # um carro com modelo por confirmar não alerta, mas fica na base (aparece no painel)
        self.assertEqual(self.ciclo("custojusto", p1=[carro("custojusto", "vago", "Carro de 2012 em bom estado", 900,
                                                          "u", ano=2012, km=100000)]), [])
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM anuncios WHERE id = 'vago'").fetchone())

    def test_regras_da_base(self):
        self.assertFalse(vale_guardar({"modelo": None, "preco": 1000, "origem": "facebook"}, self.config))
        self.assertTrue(vale_guardar({"modelo": None, "preco": 1000, "origem": "custojusto"}, self.config))
        self.assertFalse(vale_guardar({"modelo": "bmw 520", "preco": 30000, "origem": "olx"}, self.config))
        self.assertTrue(vale_guardar({"modelo": "toyota yaris", "preco": 6000, "origem": "olx"}, self.config))
        self.ciclo(p1=[yaris("velho", 6000), yaris("novo", 6100), yaris("caro", 30000),
                       carro("facebook", "trotinete", "Trotinete elétrica", 200, "https://exemplo/t")])
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
            ciclo_paginado(self.db, "olx", self.config)  # não aborta: regista e segue
        self.assertEqual(len(self.ciclo(p1=[yaris("novo", 4000)])), 1)


class DataPublicacao(unittest.TestCase):
    def test_datas_de_sites_ordenam_em_utc(self):
        self.assertEqual(texto.data_utc("2026-11-23T14:51:30Z"), "2026-11-23T14:51:30+00:00")
        self.assertEqual(texto.data_utc("2026-10-04T10:08:33+01:00"), "2026-10-04T09:08:33+00:00")
        self.assertIsNone(texto.data_utc(None))


class ComparacaoLarga(unittest.TestCase):
    def test_larga_aceita_comparaveis_sem_km_e_o_telegram_nao(self):
        from alerta.mercado import analisar
        db = abrir_db(":memory:")
        config = {**load_config(Path("config.exemplo.toml")), "min_comparaveis": 6}
        for i in range(8):  # 8 iguais, todos sem km: a comparação rigorosa não os usa
            db.execute("INSERT INTO anuncios (origem, id, modelo, preco, ano, km, combustivel) "
                       "VALUES ('olx', ?, 'toyota yaris', 4000, 2008, NULL, 'Gasolina')", (str(i),))
        alvo = carro("olx", "x", "Toyota Yaris", 2500, "u", km=150000, ano=2008, combustivel="Gasolina",
                     modelo="toyota yaris", texto="")
        self.assertIsNone(analisar(db, alvo, config)["margem"])
        larga = analisar(db, alvo, config, largo=True)
        self.assertIsNotNone(larga["margem"])
        self.assertTrue(larga["largo"] and larga["prudente_usado"])


class AnoDoTexto(unittest.TestCase):
    def test_ignora_inspecao_futuro_e_cilindrada(self):
        self.assertEqual(texto.ano_do_texto("Toyota Hilux 2.4 1988 5 velocidades"), 1988)
        self.assertIsNone(texto.ano_do_texto("Inspeção até mar 2027"))
        self.assertIsNone(texto.ano_do_texto("Yaris 2000cc"))
        self.assertEqual(texto.ano_do_texto("Yaris 2000cc de 2008"), 2008)


if __name__ == "__main__":
    unittest.main()
