# fix_cad2data — Reparo de Arquivos IFC do cad2data (ODA) para o Bonsai BIM

*[English](README.md) · **Português (Brasil)***

Um script Python que corrige defeitos estruturais em arquivos IFC gerados pelo conversor Revit-para-IFC [cad2data](https://github.com/datadrivenconstruction/cad2data-Revit-IFC-DWG-DGN), tornando-os compatíveis com o [Bonsai BIM](https://bonsaibim.org) (o add-on BIM do Blender).

Isso viabiliza um fluxo totalmente gratuito, offline e sem Revit: `.rvt` → cad2data → `fix_cad2data.py` → Bonsai.

---

## O Problema

### Sintomas

Ao abrir no Bonsai um arquivo IFC gerado pelo cad2data, três coisas acontecem:

1. **O Bonsai trava durante a importação**, com este traceback:

```
File "bonsai/tool/collector.py", line 125, in assign
    while container.is_a("IfcSpace"):
          ^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'is_a'
```

2. **As paredes aparecem com geometria faltando** — o Bonsai registra centenas de avisos:

```
Warning! Excessive voids were found and skipped for the following elements:
#80801=IfcWall(...) - 22 openings
#40324=IfcWall(...) - 24 openings
```

3. **O arquivo fica cerca de 2,4× maior** que o mesmo modelo exportado pelo exportador IFC nativo do Revit (por exemplo, 60 MB contra 25 MB).

O mesmo arquivo IFC abre corretamente no BIMvision, no FreeCAD (modo NativeIFC) e em outros visualizadores que não exigem uma hierarquia espacial completa para exibir a geometria.

### O que os visualizadores "enxergam" vs. o que o Bonsai precisa

A maioria dos visualizadores IFC trata o arquivo como uma lista plana de objetos geométricos. Eles percorrem os elementos, renderizam sua geometria e a exibem. Se o elemento tem ou não um contêiner espacial válido é irrelevante para a renderização.

O Bonsai é diferente. Ele organiza cada elemento IFC na hierarquia de coleções do Blender, que espelha a estrutura espacial do IFC:

```
IfcProject
  └── IfcSite
        └── IfcBuilding
              └── IfcBuildingStorey (Pavimento Térreo)
                    ├── IfcWall
                    ├── IfcDoor
                    └── IfcSpace (Sala 101)
                          └── IfcFurnishingElement (Mesa)
```

Para colocar um elemento na coleção certa, o Bonsai precisa subir a hierarquia espacial até encontrar um `IfcBuildingStorey`. Se um elemento está contido em um `IfcSpace`, o Bonsai sobe do `IfcSpace` até o pavimento pai. Se essa cadeia estiver quebrada — se o `IfcSpace` não tiver pai — a subida retorna `None` e o Bonsai trava.

---

## Causa Raiz: IfcRelAggregates Quebrado

### Como funciona a hierarquia espacial no IFC

O IFC usa dois tipos diferentes de relacionamento para a organização espacial:

| Relacionamento | Conecta | Finalidade |
|---|---|---|
| `IfcRelContainedInSpatialStructure` | Elementos → Contêineres espaciais | "Esta parede está no pavimento térreo" |
| `IfcRelAggregates` | Contêineres espaciais → Contêineres pais | "Esta sala faz parte do pavimento térreo" |

A distinção crítica:

- **Elementos** (paredes, portas, lajes) são **contidos** em contêineres espaciais via `IfcRelContainedInSpatialStructure`
- **Contêineres espaciais** (IfcSpace, IfcBuildingStorey) são **agregados** aos seus pais via `IfcRelAggregates`

São dois tipos de relacionamento distintos, com classes de entidade diferentes no schema IFC. Uma ferramenta que cria um mas não o outro produz um arquivo que parece correto na superfície, mas tem a cadeia quebrada.

### O que o cad2data (ODA) faz de errado

O conversor cad2data, que encapsula o SDK BimRv da Open Design Alliance (ODA), cria corretamente:

- os relacionamentos `IfcRelContainedInSpatialStructure`, colocando os elementos dentro das entidades `IfcSpace`
- as próprias entidades `IfcSpace`, com nomes e geometria

Mas ele **não cria** os relacionamentos `IfcRelAggregates` que conectam essas entidades `IfcSpace` ao seu `IfcBuildingStorey` pai. Os IfcSpaces existem como órfãos — têm filhos (os elementos dentro deles), mas nenhum pai na árvore espacial.

```
O que o exportador nativo do Revit produz:   O que o cad2data produz:

IfcBuildingStorey (Pavimento Térreo)         IfcBuildingStorey (Pavimento Térreo)
  │                                            │
  ├── IfcRelAggregates ──► IfcSpace            │  (sem IfcRelAggregates)
  │                          │                 │
  │                          ├── IfcWall       IfcSpace ◄── ÓRFÃO
  │                          └── IfcDoor         │
  │                                              ├── IfcWall
  ├── IfcWall (contido diretamente)              └── IfcDoor
  └── IfcSlab
                                               IfcWall (contido diretamente)
                                               IfcSlab
```

### Por que o Bonsai trava

O módulo `collector.py` do Bonsai atribui cada elemento a uma coleção do Blender. O trecho relevante:

```python
# collector.py, linha ~125
# Para cada elemento, encontra seu contêiner espacial
container = get_container(element)

# Se o contêiner for um IfcSpace, sobe até encontrar o pavimento
while container.is_a("IfcSpace"):
    container = get_aggregate(container)  # ← retorna None para IfcSpaces órfãos
    # Iteração seguinte: None.is_a("IfcSpace") → AttributeError
```

O laço parte do princípio de que todo `IfcSpace` tem um pai via `IfcRelAggregates`. Quando não tem, `get_aggregate()` retorna `None` e a iteração seguinte trava.

### Por que o arquivo é maior

Este é um problema separado do travamento. O exportador IFC nativo do Revit usa:

- **Geometria paramétrica** (`IfcExtrudedAreaSolid`) — uma parede é um perfil + altura, algumas centenas de bytes
- **Reuso de geometria** (`IfcMappedItem` / `IfcRepresentationMap`) — 500 portas idênticas referenciam uma única definição de geometria

O SDK BimRv da ODA usa seu Facet Modeler, que tende a emitir:

- **Representações de contorno facetadas** (`IfcFacetedBrep`) — malhas trianguladas densas em vez de sólidos paramétricos
- **Geometria por instância** — cada elemento potencialmente carrega sua própria definição completa de geometria

É por isso que o mesmo modelo tem 60 MB pelo cad2data contra 25 MB pelo exportador do Revit. A geometria é válida (o BIMvision comprova isso), apenas codificada de forma menos eficiente.

### Por que as paredes mostram "excessive voids"

O cad2data cria relacionamentos `IfcRelVoidsElement` individuais para cada abertura de porta ou janela em uma parede. Algumas paredes em um modelo típico de hotel ou residencial podem ter 10, 15 ou até 24 aberturas. O Bonsai tem um limite de operações booleanas por elemento — quando esse limite é excedido, ele ignora os recortes e renderiza a parede como uma forma sólida, sem os vãos de portas e janelas.

Isso é um **problema de exibição**, não de dados. Os dados IFC (portas, janelas e seus relacionamentos com as paredes) estão íntegros e consultáveis. Apenas os recortes não são aplicados visualmente à geometria da parede na viewport do Bonsai.

---

## O Que o Script Faz

### Visão geral

O script executa cinco correções e duas passagens de diagnóstico:

1. **Corrige a agregação de IfcSpaces órfãos** — Encontra todo `IfcSpace` sem pai via `IfcRelAggregates` e o conecta ao `IfcBuildingStorey` apropriado (correspondido por elevação quando possível, com recuo para o pavimento mais baixo).

2. **Corrige cadeias de contenção quebradas** — Para cada `IfcElement`, percorre toda a cadeia de contenção para verificar se ela chega a um `IfcBuildingStorey`. Elementos cuja cadeia está quebrada (sem contêiner algum, ou contidos em um `IfcSpace` órfão) são reatribuídos diretamente a um pavimento.

3. **Remove relacionamentos de contenção nulos** — Exclui qualquer `IfcRelContainedInSpatialStructure` cujo `RelatingStructure` seja `None`.

4. **Remove relacionamentos de agregação nulos** — Exclui qualquer `IfcRelAggregates` cujo `RelatingObject` seja `None`.

5. **Corrige IfcShapeAspect.ProductDefinitional** — Define o atributo obrigatório `ProductDefinitional` como `False` nas entidades em que ele está ausente. Este é um problema separado da ODA, que impede a execução da receita `Optimise` do IfcOpenShell.

6. **Passagem de verificação** — Após todas as correções, percorre novamente a cadeia de contenção de cada elemento para confirmar que não restaram cadeias quebradas.

7. **Diagnóstico de voids** — Relata os relacionamentos de void (contagem, distribuição) sem modificá-los.

### Detalhamento

#### Correção 1: agregação de IfcSpace

Esta é a correção crítica, a que resolve o travamento.

```python
for space in f.by_type("IfcSpace"):
    parent = ifcopenshell.util.element.get_aggregate(space)
    if parent is None:
        # Este IfcSpace é órfão — encontra o pavimento certo
        best_storey = find_storey_by_elevation(space, storeys, fallback)
        ifcopenshell.api.run("aggregate.assign_object", f,
            products=[space],
            relating_object=best_storey)
```

`aggregate.assign_object` cria o relacionamento `IfcRelAggregates` ausente, conectando o `IfcSpace` a um pavimento. Feito isso, o laço `while container.is_a("IfcSpace")` do Bonsai consegue subir do space até o pavimento pai sem esbarrar em `None`.

**Correspondência de pavimento por elevação:** O script tenta encontrar o pavimento correto comparando a coordenada Z do IfcSpace com as elevações dos pavimentos. Ele escolhe o pavimento mais alto cuja elevação esteja igual ou abaixo da posição do space. Se as coordenadas não puderem ser lidas (alguns elementos não têm posicionamento explícito), ele recorre ao pavimento de menor elevação do arquivo.

#### Correção 2: cadeias de elementos quebradas

Mesmo após a Correção 1, alguns elementos podem ter cadeias quebradas por causa de outros defeitos. Esta passagem os captura:

```python
for element in f.by_type("IfcElement"):
    container = get_container(element)
    if container is None:
        # Sem contêiner algum — atribui a um pavimento
        reassign_to_storey(element, storeys, fallback)
    elif container.is_a("IfcSpace"):
        # Verifica se a cadeia do IfcSpace chega a um pavimento
        storey = find_parent_storey(container, f)
        if storey is None:
            # A cadeia continua quebrada — ignora o IfcSpace e vai direto ao pavimento
            reassign_to_storey(element, storeys, fallback)
```

A função `find_parent_storey` percorre para cima tanto os relacionamentos de agregação quanto os de contenção, com detecção de ciclos, até encontrar um `IfcBuildingStorey` ou esgotar todos os caminhos.

#### Correções 3 e 4: relacionamentos nulos

Algumas entidades `IfcRelContainedInSpatialStructure` e `IfcRelAggregates` do arquivo têm `None` como objeto relacionado. Isso é inválido segundo o schema IFC e causa diversas falhas posteriores. O script simplesmente as remove.

#### Correção 5: IfcShapeAspect

O atributo `ProductDefinitional` é obrigatório (não opcional) em `IfcShapeAspect` no IFC4. O escritor da ODA o deixa como `None` em algumas entidades. Isso não afeta a importação no Bonsai, mas trava a receita `Optimise` do IfcOpenShell ao tentar copiar essas entidades para um novo arquivo. Defini-lo como `False` (o padrão conservador — "este shape aspect não define a forma do produto") resolve o problema.

---

## Fluxo de Trabalho

### Pré-requisitos

```bash
pip install ifcopenshell ifcpatch
```

- Python 3.10+ (testado com 3.13 e 3.14)
- IfcOpenShell 0.8.5
- cad2data Community Edition (Windows — o conversor em si)
- Bonsai BIM (Blender 4.2 LTS ou Blender 5.x)

### Passos

```
# 1. Converter o Revit para IFC (somente Windows — o cad2data é um binário Windows)
RVT2IFCconverter.exe input.rvt -o output.ifc

# 2. Reparar para o Bonsai
python fix_cad2data.py output.ifc output_fixed.ifc

# 3. (Opcional) Otimizar o tamanho do arquivo
python -m ifcpatch -i output_fixed.ifc -o output_optimized.ifc -r Optimise

# 4. Abrir no Bonsai
# File → Open IFC Project → output_fixed.ifc (ou output_optimized.ifc)
```

### O que você obtém

Após o reparo, o arquivo IFC abre no Bonsai com:

- Toda a geometria visível e corretamente posicionada
- Atribuições completas de classes IFC (IfcWall, IfcDoor, IfcSlab etc.)
- Hierarquia espacial íntegra (Site → Building → Pavimentos → Elementos)
- Propriedades e conjuntos de propriedades preservados
- Tipos (IfcWallType etc.) preservados
- Atribuições de materiais preservadas

### Limitações conhecidas

- **Avisos de "excessive voids"** — Paredes com muitas aberturas (comum em hotéis, alojamentos e hospitais) serão renderizadas sem os recortes booleanos de portas e janelas. Os dados IFC continuam íntegros; os recortes visuais são ignorados pelo importador do Bonsai por questão de desempenho. Isso é um limite do lado do Bonsai, não um defeito do arquivo.

- **Tamanho do arquivo** — O arquivo reparado mantém a geometria facetada do cad2data. Executar `ifcpatch -r Optimise` após o reparo pode reduzir o tamanho ao deduplicar geometria compartilhada, mas não vai igualar a saída do exportador nativo do Revit (que usa sólidos paramétricos e reuso via IfcMappedItem).

- **Conversão somente no Windows** — O `RVT2IFCconverter.exe` do cad2data é um binário Windows que encapsula o SDK proprietário da ODA. O script de reparo em si roda em qualquer plataforma com Python + IfcOpenShell.

- **Cobertura de versões do Revit** — O cad2data suporta arquivos do Revit 2015–2026. Formatos mais antigos não são suportados pelo SDK BimRv da ODA.

- **Não é certificado pela buildingSMART** — A saída IFC do cad2data não tem certificação da buildingSMART. O próprio FAQ da ODA afirma que a certificação está planejada, mas ainda não foi obtida. O script de reparo corrige os defeitos estruturais conhecidos, mas não garante conformidade com o schema em todos os casos extremos.

---

## Contexto Técnico

### Por que esse defeito existe

O conversor cad2data encapsula o SDK BimRv da Open Design Alliance (ODA), uma biblioteca comercial que faz engenharia reversa do formato binário proprietário `.rvt` do Revit (um contêiner Microsoft Compound File / OLE). A ODA lê o banco de dados de elementos e a geometria em cache do arquivo Revit e os mapeia para entidades IFC.

O defeito — `IfcRelAggregates` ausente para IfcSpaces — sugere que o escritor IFC da ODA trata corretamente o relacionamento "elemento → contêiner espacial" (IfcRelContainedInSpatialStructure), mas não cria o relacionamento "contêiner espacial → contêiner espacial pai" (IfcRelAggregates) para IfcSpaces. Isso provavelmente acontece porque:

1. O Revit armazena internamente ambientes e espaços de forma diferente da hierarquia espacial do IFC
2. O mapeamento do modelo de ambientes/espaços do Revit para o IfcRelAggregates do IFC está incompleto no escritor da ODA
3. Como os próprios visualizadores da ODA (OpenIFCViewer, BricsCAD) não exigem cadeias de agregação completas, o defeito nunca foi detectado nos testes deles

O exportador IFC do próprio Revit (o projeto open source [revit-ifc](https://github.com/Autodesk/revit-ifc)) trata isso corretamente, porque tem acesso completo à API do Revit e foi especificamente construído e certificado para interoperabilidade IFC.

### Ferramentas afetadas

| Ferramenta | Comportamento com IFC do cad2data |
|---|---|
| BIMvision | Abre corretamente — não percorre cadeias de agregação |
| FreeCAD (NativeIFC) | Abre corretamente — renderiza a geometria diretamente do IFC |
| FreeCAD (Import) | Importação parcial — parte dos dados espaciais se perde |
| IfcOpenShell (ifcconvert) | Converte a geometria corretamente — não processa coleções |
| Bonsai BIM | **Trava** — exige cadeia de agregação completa para atribuir as coleções |
| Solibri, Navisworks | Não testado — provavelmente depende de como processam a hierarquia espacial |

### Funções da API do IfcOpenShell utilizadas

| Função | Finalidade no script |
|---|---|
| `ifcopenshell.open()` | Ler o arquivo IFC |
| `ifcopenshell.util.element.get_container()` | Encontrar o contêiner espacial do elemento via `IfcRelContainedInSpatialStructure` |
| `ifcopenshell.util.element.get_aggregate()` | Encontrar o pai do elemento espacial via `IfcRelAggregates` |
| `ifcopenshell.api.run("aggregate.assign_object")` | Criar o relacionamento `IfcRelAggregates` ausente |
| `ifcopenshell.api.run("spatial.assign_container")` | Criar/atualizar o `IfcRelContainedInSpatialStructure` |
| `f.remove()` | Excluir entidades de relacionamento inválidas |
| `f.write()` | Salvar o arquivo reparado |

---

## Relação com o Ecossistema IFC

### Referências do schema IFC

- **IfcRelAggregates** — IFC4 §5.1.3.1 — Decompõe um elemento de estrutura espacial em partes. É o tipo de relacionamento que o cad2data deixa de criar para os IfcSpaces.
- **IfcRelContainedInSpatialStructure** — IFC4 §5.1.3.5 — Contém elementos dentro de um elemento de estrutura espacial. O cad2data cria estes corretamente.
- **IfcBuildingStorey** — IFC4 §5.1.2.2 — O elemento de estrutura espacial que representa um pavimento. O alvo da agregação ausente.
- **IfcSpace** — IFC4 §5.1.2.4 — Um elemento espacial que representa um ambiente ou zona. A entidade órfã na saída do cad2data.
- **IfcShapeAspect** — IFC4 §8.11.3.2 — `ProductDefinitional` é um atributo BOOLEAN obrigatório. A ODA o deixa nulo.

### Ferramentas relacionadas do IfcOpenShell

- **ifcpatch Optimise** — Deduplica definições de geometria compartilhadas. Falha em arquivos do cad2data por causa do defeito no IfcShapeAspect (corrigido por este script).
- **ifcpatch TessellateElements** — Retessela a geometria através dos bindings Python do IfcOpenShell. Não corrige o travamento, porque o travamento está na estrutura espacial, não na geometria.
- **ifcconvert** — Converte IFC para OBJ/DAE e outros formatos. Funciona corretamente em arquivos do cad2data porque processa apenas geometria, não a hierarquia espacial.
- **ifcopenshell.validate** — Módulo de validação de schema. Consegue detectar o atributo `ProductDefinitional` ausente, mas não o defeito semântico dos IfcSpaces órfãos (que é estruturalmente válido segundo o schema — apenas sem os relacionamentos esperados).

---

## Notas de Desenvolvimento

### Testes

Para verificar que a correção funciona:

1. Converta qualquer arquivo Revit (2015–2026) usando o cad2data
2. Tente abrir o IFC resultante no Bonsai — confirme que ele trava
3. Execute o `fix_cad2data.py` sobre o arquivo
4. Abra o arquivo corrigido no Bonsai — confirme que ele carrega sem travar
5. Verifique, no painel de decomposição espacial do Bonsai, que todos os elementos estão corretamente atribuídos aos pavimentos

### Casos extremos a investigar

- **Arquivos com múltiplas edificações** — O cad2data separa corretamente os elementos entre várias entidades IfcBuilding? A seleção do pavimento de recuo do script talvez precise levar isso em conta.
- **Modelos vinculados** — O log do console do Bonsai sugere que o arquivo do hotel continha modelos vinculados/referenciados. Cada modelo vinculado pode precisar de reparo independente.
- **Limites de ambiente (IfcSpace boundaries)** — O script não verifica os relacionamentos `IfcRelSpaceBoundary`. Eles definem quais elementos delimitam um ambiente (paredes, pisos, forros). Se o cad2data também falhar em criá-los, análises baseadas em ambientes (energia, tabelas de acabamento) ficarão incompletas.
- **Precisão da elevação dos pavimentos** — A correspondência por elevação usa a heurística simples "pavimento mais alto igual ou abaixo do Z do elemento". Isso pode atribuir elementos incorretamente em edifícios com mezaninos, meios-níveis ou pés-direitos fora do padrão.
- **IFC2x3 vs. IFC4** — O cad2data pode exportar os dois. O script usa a API do IfcOpenShell, que lida com ambos os schemas, mas a estrutura da hierarquia espacial difere ligeiramente entre eles. Recomenda-se testar com saída IFC2x3.

### Melhorias possíveis

- **Processamento em lote** — Aceitar um diretório de arquivos IFC e processar todos de uma vez.
- **Relatório de validação** — Gerar um relatório estruturado (JSON, CSV) do que foi corrigido, para integração em fluxos de auditoria.
- **Configuração do limite de voids** — Permitir definir o limite de voids excessivos do Bonsai, ou mesclar previamente os voids no arquivo para evitar o aviso.
- **Otimização de geometria** — Detectar e mesclar definições de geometria duplicadas criadas pelo cad2data (o que o `ifcpatch Optimise` faz, mas sem o travamento do IfcShapeAspect).
- **Integração com CI** — Rodar como uma GitHub Action que repara automaticamente os arquivos IFC de um repositório.
- **Comparação com a exportação do Revit** — A partir do mesmo arquivo .rvt de origem, comparar a saída do cad2data com a exportação nativa do Revit e relatar as diferenças em contagem de entidades, tipos de geometria e completude da estrutura espacial.

---

## Contexto: O Fluxo Sem Revit

Esta ferramenta existe para atender arquitetos e profissionais de BIM que recebem arquivos `.rvt` mas não têm licença do Revit. O fluxo completo:

```
arquivo .rvt (de um colaborador)
    │
    ▼
cad2data RVT2IFCconverter.exe     ← Gratuito, offline, local
    │
    ▼
IFC bruto (hierarquia espacial quebrada)
    │
    ▼
fix_cad2data.py                    ← Este script
    │
    ▼
IFC reparado
    │
    ▼
Bonsai BIM (Blender)               ← Gratuito, código aberto
    │
    ▼
Documentação, auditoria, coordenação
```

Todas as etapas rodam localmente. Sem contas na nuvem, sem assinatura da Autodesk, sem uploads. O modelo nunca sai da máquina — o que é relevante para projetos com requisitos de confidencialidade.

---

## Licença

GPL-3.0 — veja [LICENSE](LICENSE).

## Créditos

- [cad2data](https://github.com/datadrivenconstruction/cad2data-Revit-IFC-DWG-DGN), da DataDrivenConstruction (Artem Boiko) — conversão Revit para IFC
- [IfcOpenShell](https://ifcopenshell.org) — biblioteca de processamento IFC
- [Bonsai BIM](https://bonsaibim.org) — add-on BIM para o Blender
- Metodologia de diagnóstico desenvolvida por meio de testes sistemáticos da saída do cad2data em múltiplos visualizadores IFC
