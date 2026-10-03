# eddy-ng — myfork (correções)

Fork baseado em [vvuk/eddy-ng](https://github.com/vvuk/eddy-ng) + PR #150 (calibração 3D de temperatura)
+ mesh adaptativo (`EDDYNG_BED_MESH_EXPERIMENTAL ADAPTIVE=1`, ideia da issue
[Kalico #828](https://github.com/KalicoCrew/kalico/issues/828)).

Build atual: `myfork-fix6-2026-10-03` (aparece no `klippy.log` ao iniciar:
`probe_eddy_ng build ... loaded from <caminho>` — se a linha não aparecer, o Klipper está
carregando outro arquivo).

## Sintomas corrigidos

| Sintoma | Causa | Correção |
|---|---|---|
| `Internal error on command:"EDDYNG_BED_MESH_EXPERIMENTAL"` → shutdown, só `FIRMWARE_RESTART` resolvia | `_adaptive_mesh` saía cedo (sem objetos / `ADAPTIVE=0`) sem refazer `_mesh_path`; o caminho adaptivo da impressão anterior ficava preso e `_set_bed_mesh` estourava com `IndexError` | Contagem, limites **e caminho** são recalculados a cada chamada; objetos fora da área/área vazia → mesh completo; qualquer exceção inesperada vira erro de comando recuperável (sem shutdown) |
| Tap passa a falhar sempre (`Already sampling!`) até reiniciar | `if "Sensor error" or ... in str(err)` é sempre verdadeiro (também no upstream): todo erro era engolido sem o evento `gcode:command_error`; se o erro ocorria antes de `homing_move_end`, o sampler/endstop/MCU ficavam armados | `recover_probe_state()` (sampler, endstop, trsync, `finish_home`) chamado antes/depois do tap, antes do mesh, no `command_error` e dentro de `start_sampler()` (auto-recuperação); lista explícita de erros que justificam nova tentativa |
| Tap com `Watchdog Error` / `No samples received` depois de imprimir (câmara quente) | Drive current alto (18) escolhido na calibração a frio deixa o LDC1612 em erro de watchdog quando o sensor esquenta | Fallback em cascata (abaixo) e o wizard continua escolhendo o maior drive current que funciona (como no upstream), para o fallback ter para onde descer |
| Heights vs. gatilho do MCU inconsistentes com `sensor_temp_sensor` | `height_to_freq` ignorava o drift que `freq_to_height` aplica (PR #150) | `height_to_freq` aplica o inverso exato do drift |

## Comportamento novo do tap

* **Fallback em cascata:** se o tap falhar **2 vezes seguidas por erro de sensor**
  (`Sensor error`, `Watchdog`, `No samples received`, `Communication timeout`), o tap desce para o
  próximo drive current calibrado abaixo (ex.: 18 → 17 → 16 → 15) e avisa no log:
  `sensor keeps failing at tap drive current X; stepping down to Y for this tap`.
  Cada passo ganha +2 tentativas, então não consome o `tap_max_samples`.
  O fallback vale só para aquele comando; o valor salvo em `tap_drive_current` não muda.
* Se já estiver no menor drive current calibrado não há para onde descer: só as tentativas normais.
* **Wizard (`PROBE_EDDY_NG_SETUP`)**: inalterado em relação ao PR #150/upstream — salva o **maior** drive current
  que passa em ≥3/5 taps. É de propósito: assim o fallback sempre tem valores abaixo para tentar.
* Z do tap muda com o drive current (menos sensível → stddev um pouco maior). Confira o primeiro layer
  depois de mudar.

## Instalação / atualização

1. Copie `eddy-ng/probe_eddy_ng.py` (e os demais arquivos, se quiser) para o diretório do eddy-ng
   usado pelo Klipper (o que o `install.sh` linkou em `klippy/extras`).
2. `FIRMWARE_RESTART`.
3. Confirme no `klippy.log` a linha `probe_eddy_ng build myfork-fix6-2026-10-03 loaded from ...`.

> **`/bin/bash^M: bad interpreter`** — o `install.sh` ficou com final de linha do Windows (CRLF), geralmente por
> descompactar/commitar no Windows com `autocrlf`. Corrija na Pi com `sed -i 's/\r$//' install.sh install.py` e rode
> `./install.sh` (ou `bash install.sh`). O repositório agora tem `.gitattributes` forçando LF.

## Recomendação de configuração

```
[probe_eddy_ng btt_eddy]
tap_drive_current: 16   # ou o valor que o wizard salvar; evite valores no limite a quente
```

## Testes

```
pip install pytest numpy
cd eddy-ng && python3 -m pytest test_probe_eddy_ng.py test_bed_mesh_scan.py
```

40 testes. `test_bed_mesh_scan.py` (novo) cobre: mesh adaptivo repetido/alternado (reprodução do `IndexError`),
recuperação de sampler preso e escolha do drive current de fallback. `test_probe_eddy_ng.py` (original) tinha o
caminho fixo da máquina do autor; agora localiza o `probe_eddy_ng.py` ao lado dele. Não substituem teste na impressora.

## Arquivos alterados

* `probe_eddy_ng.py` — todas as correções acima
* `test_bed_mesh_scan.py` — novos testes
* `test_probe_eddy_ng.py` — caminho do módulo agora relativo ao arquivo
