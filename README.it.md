# Galaxy80 QMK (non ufficiale)

Firmware aperto per la tastiera **Epomaker x Feker Galaxy80**, basato su QMK (fork SonixQMK): USB cablato, 2.4G e
Bluetooth, RGB per tasto, VIA, editor dei colori, funzioni da gaming e indicatore di batteria sulla barra laterale.
La documentazione completa e' in inglese: [README.md](README.md) e la cartella [docs/](docs/).

> **Progetto non ufficiale.** Non ha alcun legame con Epomaker, Feker, Keychron o Sonix e non e' approvato da loro.
> Flashare sostituisce il firmware originale, lo fai a tuo rischio e senza garanzia (GPL-2.0-or-later). In questo
> repository non c'e' nessun file del produttore (firmware, updater, driver, manuale).

## Stato dei test

* **Provato su hardware**: USB cablato, 2.4G, Bluetooth, RGB, editor colori, barra laterale, funzioni gaming, VIA.
* **Provato solo nell'emulatore** (novita' della 1.0.0, mai provate su una tastiera vera): spegnimento delle luci
  dopo l'inattivita' (cavo e wireless, predefinito 5 min), sleep profondo wireless (**SPERIMENTALE**, predefinito "Never"),
  indicatore di batteria Fn+Space, salvataggio VIA stabile, Apply/Discard dei colori, protezione SEC-1, NKRO wireless,
  modalita' Snap Click, tasti per macOS/Windows, Chordal Hold e Flow Tap per i tasti `MT()`/`LT()`, Shift+Backspace = Canc, tasti QMK "magic". L'elenco e la lista di prove da fare: [docs/hardware-test-checklist.md](docs/hardware-test-checklist.md).

## E' la mia scheda?

Solo una scheda e' stata provata: ID USB originale **05AC:024F**, firmware originale **v1.10**, chip SN32F248BF, PCB
"galaxy80-RGB-HFD" 20231124. **Se qualcosa non coincide, non flashare** e apri una segnalazione "Hardware revision".
Dettagli: [docs/is-this-my-board.md](docs/is-this-my-board.md). Dopo il flash l'ID e' **4758:0080**.

## Installazione

Dettagli: [docs/install.md](docs/install.md). In breve:

1. Scarica da Releases `galaxy80-vX.Y.Z-via.bin` e controlla l'hash con `SHA256SUMS.txt`. Chiudi VIA e il software del produttore.
2. Cursore su **OFF** (cablato), cavo dati diretto al PC.
3. **Entra in ISP**: scollega il cavo, **aspetta almeno 30 s**, tieni premuto **P** (o **Esc**), collega il cavo e tieni premuto 2-3 s.
   In Gestione dispositivi deve comparire **0C45:7040**. (Con il firmware originale installato non basta P: tieni premuti
   insieme F10, 0, P, ;, . e Alt destro.)
4. Con [SonixFlasherC](https://github.com/SonixQMK/SonixFlasherC) 3.0.0, su una **copia** del file:

       sonixflasher -v 0c45/7040 -i
       sonixflasher -v 0c45/7040 -f galaxy80-vX.Y.Z-via.bin

   Il primo comando e' di sola lettura: deve mostrare un chip della famiglia SN32F240B da 64 KB, altrimenti fermati. Non usare
   altre opzioni (mai `-o`, `-j`, `-k`, `-r`). Non staccare il cavo durante la scrittura.
5. La tastiera si ripresenta come 4758:0080. **Ogni flash cancella impostazioni, keymap e colori**; al primo avvio si parte dai valori di fabbrica.
6. VIA: apri usevia.app (Chrome/Edge), attiva *Show Design tab* nelle impostazioni, carica `galaxy80.json`, autorizza il dispositivo.

## Recupero

Dettagli: [docs/recovery.md](docs/recovery.md). L'ISP della ROM del chip non si puo' sovrascrivere: se fai un vero spegnimento
(cursore OFF, cavo staccato, 30 s) e tieni P o Esc all'inserimento, si entra sempre in ISP, qualunque cosa ci sia in flash. Da
firmware in funzione: **Fn + Shift destro + Esc**. Un fault o un halt del firmware finiscono da soli in ISP; un blocco
fa invece ripartire la tastiera con il watchdog (in ISP solo se Esc o P sono premuti in quel momento). Il ritorno al
firmware originale **non e' verificato** (vedi il README inglese).

## Tasti Fn

Tieni premuto **Fn** (tra Alt destro e Menu):

| Fn + | Funzione | Fn + | Funzione |
|---|---|---|---|
| Esc (con Shift destro) | bootloader ROM | F1..F12 | tasti multimediali |
| Win sinistro | blocco Win | Backspace (3 s) | reset di fabbrica |
| Invio | effetto successivo | \ | colore successivo |
| Su / Giu' | luminosita' | Destra / Sinistra | velocita' effetto |
| T | luci tasti on/off | Shift destro | modalita' barra laterale |
| Ins | tutti i LED on/off | N | NKRO on/off |
| L / D | blocca schermo / mostra desktop | G | WASD <-> frecce |
| Tab / Bloc Maiusc | blocca Alt+Tab / Alt+F4 | S | Snap Tap on/off |
| K | editor colori per tasto | Pausa | modalita' gioco |
| C | Caps Word | Q / W / E / R | Bluetooth 1-3 / 2.4G (cursore ON); tieni 3 s per l'accoppiamento |
| Spazio (tieni) | livello batteria sulla barra | | |

Nell'editor colori: **Fn+Invio** salva (Apply), **Fn+Canc** annulla (Discard). I colori dipinti non vengono salvati finche'
non premi Apply. Tabella completa: [docs/keys.md](docs/keys.md).

**Salvataggio (immagine `via`)**: impostazioni, tasti Fn e luci vengono scritti circa 1 s dopo l'ultima modifica (il LED 5
della barra smette di lampeggiare in magenta); i colori dipinti solo con Apply (lampeggio verde). Stacca il cavo dopo e non
perdi niente; staccandolo prima perdi solo cio' che non era ancora salvato. Dal 2026-10-21 un'interruzione durante la pulizia
della flash non azzera piu' tutto ([docs/stability.md](docs/stability.md#power-cuts-and-unplugging)).

**Luci e sleep** (VIA, menu Power): "Lights Off After" spegne i LED dopo 1 / 5 / 10 / 30 min senza tasti (cavo e wireless,
predefinito 5 min, oppure Never); la tastiera continua a funzionare e qualsiasi tasto, un comando VIA o il cursore wired/wireless riaccende le luci (collegare o scollegare il caricabatterie no). Con le luci spente
anche gli indicatori (carica, batteria scarica) sono spenti. "Deep Sleep After" (solo wireless, **sperimentale**, predefinito
Never) mette la tastiera in sleep profondo 5 / 10 / 30 / 60 min dopo l'ultimo tasto; la sveglia un tasto o il cursore.
Effetti RGB: Solid, Rainbow, Per-key.

Snap Tap e' un interruttore **manuale** (Fn+S), spento dopo un reset di fabbrica: alcuni giochi lo vietano, decidi tu per ogni gioco.

## Sviluppo e licenza

Compilazione e test (emulatore, senza hardware): [docs/building.md](docs/building.md), [docs/testing.md](docs/testing.md).
Licenza GPL-2.0-or-later; i binari rilasciati sono distribuiti sotto GPL-3.0 ([NOTICE.md](NOTICE.md)).
Sicurezza: [SECURITY.md](SECURITY.md).
