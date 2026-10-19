"""Dedicated frozen F-profile experiment. Default mock never constructs a SDK."""
from argparse import ArgumentParser
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'),str(ROOT)]
from linkloom.evaluation.task_profile_ab import read, run


def main():
    parser = ArgumentParser(__doc__)
    parser.add_argument('--mode',choices=('mock','replay','live'),default='mock')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--replay',type=Path)
    parser.add_argument('--enable-live',action='store_true')
    parser.add_argument('--grant',type=Path)
    args = parser.parse_args()
    if args.mode == 'live' and not (args.enable_live and args.grant):
        parser.error('LIVE_AUTHORIZATION_REQUIRED: --enable-live and exact separately approved --grant')
    if args.mode != 'live' and (args.enable_live or args.grant):
        parser.error('LIVE flags cannot be used in offline modes')
    report = run(args.output,mode=args.mode,replay=args.replay,grant=read(args.grant) if args.grant else None)
    print({k:report[k] for k in ('mode','counts','real_provider_requests','REAL_MODEL_SEMANTIC_EFFECT')})


if __name__ == '__main__':
    main()
