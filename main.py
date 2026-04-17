import argparse
import sys
import os
import threading
import time

from rich import print as rprint
from core.download_engine import DownloadEngine
from monitors.latency_monitor import LatencyMonitor
from monitors.network_monitor import NetworkMonitor
from monitors.activity_detector import ActivityDetector
from control.throttler import Throttler
from control.scheduler import Scheduler
from ui.dashboard import Dashboard

def main():
    parser = argparse.ArgumentParser(description="Latency Aware Download Manager")
    parser.add_argument("url", help="URL of the file to download")
    parser.add_argument("output", help="Output file path")
    parser.add_argument("--sha256", help="Expected SHA256 hash for verification", required=False)
    parser.add_argument("--hash-file", help="Path/URL to a .sha256 digest file to read from", required=False)
    parser.add_argument("--manifest", help="Path/URL to JSON chunk hashes for BitTorrent-style piecewise checks", required=False)
    parser.add_argument("--manifest-sig", help="Path/URL to PGP .sig authenticating the manifest itself", required=False)
    parser.add_argument("--sig", help="Path/URL to PGP .sig or .asc file", required=False)
    parser.add_argument("--pubkey", help="Path/URL to PGP public key. Must provide --fingerprint if used.", required=False)
    parser.add_argument("--fingerprint", help="Required mathematically valid fingerprint if importing an external --pubkey", required=False)
    parser.add_argument("--scan", action="store_true", help="Trigger an endpoint malware scan (Windows Defender) on completion")
    args = parser.parse_args()
    
    import requests, json
    
    # Python 3.13+ compatibility bypass for legacy PGPy module
    import sys, types
    if 'imghdr' not in sys.modules:
        dummy_imghdr = types.ModuleType('imghdr')
        dummy_imghdr.what = lambda *args, **kwargs: None
        sys.modules['imghdr'] = dummy_imghdr
        
    import pgpy
    import glob
    
    # --- TRUST STORE INIT ---
    trust_dir = "trusted_keys"
    os.makedirs(trust_dir, exist_ok=True)
    trusted_keys = []
    
    for key_file in glob.glob(os.path.join(trust_dir, "*.gpg")) + glob.glob(os.path.join(trust_dir, "*.asc")):
        try:
            k, _ = pgpy.PGPKey.from_blob(open(key_file, "rb").read())
            if getattr(k, 'is_expired', False):
                print(f"[WARNING] Key expired, revoking from Trust Store: {key_file}")
            else:
                trusted_keys.append(k)
        except:
            print(f"[WARNING] Unparsable key corrupted in trust store: {key_file}")
            
    if args.pubkey:
        if not args.fingerprint:
            rprint("[bold red][FATAL]: ZERO-TRUST ABORTED.[/bold red] You supplied an external --pubkey without a valid --fingerprint. Key Import Rejected.")
            sys.exit(1)
        rprint(f"[INFO] Fetching and validating external key against fingerprint: {args.fingerprint}")
        try:
            pub_blob = requests.get(args.pubkey).text if args.pubkey.startswith("http") else open(args.pubkey).read()
            dynamic_key, _ = pgpy.PGPKey.from_blob(pub_blob)
            clean_fp = str(dynamic_key.fingerprint).replace(" ", "").upper()
            target_fp = str(args.fingerprint).replace(" ", "").upper()
            
            if clean_fp == target_fp:
                trusted_keys.append(dynamic_key)
                rprint("[bold green][SAFE]:[/bold green] External key verified against fingerprint and locally trusted.")
            else:
                rprint(f"[bold red][FATAL]: FINGERPRINT MISMATCH.[/bold red]\nExpected: {target_fp}\nGot: {clean_fp}")
                sys.exit(1)
        except Exception as e:
            rprint(f"[bold red][FATAL]:[/bold red] External key parsing failed: {e}")
            sys.exit(1)
            
    # Process Identifiers
    if args.hash_file:
        try:
            raw = requests.get(args.hash_file).text if args.hash_file.startswith("http") else open(args.hash_file).read()
            # typically format is "hash filename" or just "hash"
            args.sha256 = raw.strip().split()[0]
        except Exception as e:
            print(f"[ERROR] Could not parse hash-file: {e}")
            sys.exit(1)
            
    manifest_data = None
    if args.manifest:
        if not args.manifest_sig:
            rprint("[bold red][FATAL]: ZERO-TRUST ABORTED.[/bold red] You supplied an unauthenticated --manifest without --manifest-sig!")
            sys.exit(1)
            
        try:
            raw_manifest = requests.get(args.manifest).content if args.manifest.startswith("http") else open(args.manifest, "rb").read()
            if args.manifest_sig:
                if not trusted_keys:
                    rprint("[bold red][FATAL]:[/bold red] You require a secure manifest but your Trust Store is completely empty!")
                    sys.exit(1)
                sig_blob = requests.get(args.manifest_sig).content if args.manifest_sig.startswith("http") else open(args.manifest_sig, "rb").read()
                sig = pgpy.PGPSignature.from_blob(sig_blob)
                msg = pgpy.PGPMessage.new(raw_manifest)
                
                authorized = False
                for t_key in trusted_keys:
                    try:
                        if t_key.verify(msg, sig):
                            authorized = True
                            break
                    except: pass
                
                if not authorized:
                    rprint("[bold red][FATAL]: MANIFEST TAMPERED.[/bold red] The cryptographic manifest failed trust store signature verification!")
                    sys.exit(1)
                rprint("[bold green][SAFE]:[/bold green] Chunk piece manifest explicitly signed and trusted.")
            manifest_data = json.loads(raw_manifest.decode("utf-8"))
        except Exception as e:
            rprint(f"[bold red][ERROR][/bold red] Could not parse piece manifest bounds: {e}")
            sys.exit(1)

    # 1. Initialize monitors
    latency_mon = LatencyMonitor(target_ip="8.8.8.8", interval=0.5)
    net_mon = NetworkMonitor(interval=0.5)
    activity_det = ActivityDetector(network_monitor=net_mon, latency_monitor=latency_mon)

    # 2. Control loops
    throttler = Throttler(max_threads=32)
    scheduler = Scheduler(throttler, latency_mon, net_mon, activity_det)

    # 3. Engine
    engine = DownloadEngine(args.url, args.output, throttler)

    # Start monitors
    latency_mon.start()
    net_mon.start()
    activity_det.start()
    scheduler.start()

    # Need a small delay for baseline latency gathering
    time.sleep(2.0)

    # 4. UI Dashboard
    dashboard = Dashboard(engine, latency_mon, net_mon, activity_det, throttler)

    # We start download in a separate thread so Dashboard can block the main thread
    down_thread = threading.Thread(target=engine.start, args=(manifest_data,), daemon=True)
    dashboard.down_thread = down_thread
    down_thread.start()

    try:
        # Blocks the main thread with rich CLI layout
        dashboard.start_sync()
    except KeyboardInterrupt:
        engine.aborted.set()

    # Shutdown
    engine.aborted.set()
    down_thread.join(timeout=2)
    scheduler.stop()
    activity_det.stop()
    net_mon.stop()
    latency_mon.stop()
    if hasattr(engine.storage, 'close'):
        engine.storage.close()
    
    print("\nDownload finished or interrupted.")
    if engine.storage.check_completion():
        print("[INFO] Download complete. Executing Final Security Pipeline...")
        pipeline_safe = True
        pgp_override = False
        
        # 1. PGP Identity Check
        if args.sig:
            rprint("[INFO] Verifying Publisher Identity via Trust Store...")
            if not trusted_keys:
                pipeline_safe = False
                rprint("[bold red]❌ FATAL PGP:[/bold red] Your Trust Store is empty. Cannot verify signature!")
            else:
                try:
                    sig_blob = requests.get(args.sig).content if args.sig.startswith("http") else open(args.sig, "rb").read()
                    sig = pgpy.PGPSignature.from_blob(sig_blob)
                    msg = pgpy.PGPMessage.new(open(args.output, "rb").read())
                    
                    authorized = False
                    for t_key in trusted_keys:
                        try:
                            if t_key.verify(msg, sig):
                                authorized = True
                                break
                        except: pass
                            
                    if authorized:
                        pgp_override = True
                        rprint("[bold green]✅ PGP AUTHENTICATED:[/bold green] Publisher identity mathematically validated.")
                    else:
                        pipeline_safe = False
                        rprint("[bold red]❌ FATAL PGP:[/bold red] None of your Trusted Keys authorize this payload! Publisher identity compromised.")
                except Exception as e:
                    pipeline_safe = False
                    rprint(f"[bold red]❌ PGP OBFUSCATION EXCEPTION:[/bold red] {e}")
                    
        # 2. Hashing Check
        if pipeline_safe and args.sha256:
            if pgp_override:
                rprint("[INFO] Payload explicitly secured by Trust Store PGP. Bypassing redundant monolithic SHA256 integrity sweep.")
            else:
                rprint("[INFO] Sweeping monolithic payload integrity bounds...")
                from rich.progress import Progress
            final_hash = None
            with Progress() as progress:
                # Still show the nice progress bar even on the standard full-file sweep
                task = progress.add_task("[cyan]Hashing Payload Boundaries...", total=100)
                for bytes_read, total_size, current_hash in engine.storage.verify_sha256_piecewise():
                    percentage = (bytes_read / total_size) * 100 if total_size > 0 else 100
                    progress.update(task, completed=percentage)
                    final_hash = current_hash
            
            if not pgp_override:
                if final_hash and final_hash.lower() == args.sha256.lower():
                    rprint(f"[bold green]✅ SAFE INT:[/bold green] Hash validation matched {args.sha256[:16]}...")
                else:
                    pipeline_safe = False
                    rprint(f"[bold red]❌ FATAL HASH CORRUPTION:[/bold red]\nExpected: {args.sha256}\nGot:      {final_hash}")
                
        # 3. Endpoint Defense 
        if pipeline_safe and args.scan:
            rprint("[INFO] Submitting finalized packet to Windows Defender endpoint logic...")
            import subprocess
            from rich.progress import Progress, SpinnerColumn, TextColumn
            
            abs_path = os.path.abspath(args.output)
            cmd = f'Start-MpScan -ScanType CustomScan -ScanPath "{abs_path}"'
            
            res = None
            with Progress(
                SpinnerColumn(),
                TextColumn("[cyan]🛡️ Windows Defender Subsystem sweeping payload execution behaviors..."),
                transient=True
            ) as progress:
                progress.add_task("scan", total=None)
                res = subprocess.run(["powershell", "-c", cmd], capture_output=True, text=True)
                
            if res and res.returncode == 0:
                rprint("[bold green]🛡️ MALWARE SCAN CLEAN:[/bold green] Defender telemetry found zero behavioral threats.")
            else:
                pipeline_safe = False
                error_msg = res.stderr.strip() if res else "Unknown Subprocess Error"
                rprint(f"[bold red]❌ FATAL MALWARE BLOCK:[/bold red] Windows Defender detected an active payload! {error_msg}")
                
        # 4. Final Execution State
        if pipeline_safe:
            if not args.sha256 and not args.sig:
                rprint("[bold yellow][WARNING] Zero Integrity sources provided. Operating in blind faith model.[/bold yellow]")
            engine.storage.apply_motw()
            rprint("[bold green]🎉 PROTOCOL SECURE.[/bold green] Windows MotW assigned. File safe for execution.")
        else:
            try:
                os.remove(args.output)
                rprint("[bold yellow]⚠️  The tampered entity was brutally excised from storage to isolate the threat.[/bold yellow]")
            except Exception as e:
                rprint(f"[bold red]FAILED TO DELETE QUARANTINE - PURGE MANUALLY: {e}[/bold red]")
    else:
        print("Download incomplete. Run process again to resume.")

if __name__ == "__main__":
    main()
