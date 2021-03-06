#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
Takes a variant m3u8 playlist, creates I-frame playlists for it, and
creates an updated master playlist with links to the new I-frame playlists.
"""

import os.path
import urlparse as url_parser
import sys
import argparse
import subprocess
import json
import m3u8_gzip as m3u8
import logging
logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)


class GenericError(Exception):
    """
    Generic error
    """
    def __str__(self):
        return "%s(%s)" % (self.__class__.__name__, self.args)


class BadPlaylistError(GenericError):
    """
    Error raised when the playlist is unusable
    """


class PlaylistLoadError(GenericError):
    """
    Error raised when loading the playlist failed
    """


class DataError(GenericError):
    """
    Error raised when reading transport stream data failed
    """


class DependencyError(GenericError):
    """
    Error raised when a dependency is not installed
    """


def update_for_iframes(url):
    logging.info('update_for_iframes')
    """
    Returns an updated master playlist and new I-frame playlists
    """
    try:
        master_playlist = m3u8.load(url)
    except IOError:
        raise PlaylistLoadError('Invalid url')

    if not master_playlist or not master_playlist.is_variant:
        raise BadPlaylistError('Not a variant playlist')

    master_playlist.iframe_playlists[:] = []

    uri = url.split('/')[-1]
    result = {'master_uri': uri,
              'master_content': None,
              'iframe_playlists': []}

    for playlist in master_playlist.playlists:
        iframe_playlist, data = create_iframe_playlist(playlist)
        if iframe_playlist is None or data is None:
            continue
        master_playlist.add_iframe_playlist(iframe_playlist)
        result['iframe_playlists'].append(data)

    result['master_content'] = master_playlist.dumps()
    return result


def create_iframe_playlist(playlist):
    """
    Creates a new I-frame playlist.
    """
    logging.info('create_iframe_playlist')
    try:
        subprocess.check_output('ffprobe -version', stderr=subprocess.STDOUT,
                                shell=True)
    except subprocess.CalledProcessError:
        raise DependencyError('FFmpeg not installed correctly')

    iframe_playlist = generate_m3u8_for_iframes()

    total_bytes = 0
    total_duration = 0

    try:
        stream = m3u8.load(playlist.absolute_uri)
    except IOError:
        raise PlaylistLoadError('Invalid stream url')
    except AttributeError:
        raise BadPlaylistError('Invalid playlist - no absolute uri')

    for segment in stream.segments:

        iframe_segments, s_bytes, s_duration = create_iframe_segments(segment)

        for iframe_segment in iframe_segments:
            iframe_playlist.add_segment(iframe_segment)

        total_bytes += s_bytes
        total_duration += s_duration

    if total_bytes != 0 and total_duration != 0:
        iframe_bandwidth = str(int(total_bytes / total_duration * 8))
    else:
        return (None, None)

    iframe_codecs = convert_codecs_for_iframes(playlist.stream_info.codecs)
    stream_info = {'bandwidth': iframe_bandwidth,
                   'codecs': iframe_codecs}
    iframe_playlist_uri = playlist.uri.replace('.m3u8', '-iframes.m3u8')

    new_iframe_playlist = m3u8.IFramePlaylist(base_uri=playlist.base_uri,
                                              uri=iframe_playlist_uri,
                                              iframe_stream_info=stream_info)

    return (new_iframe_playlist, {'uri': iframe_playlist_uri,
                                  'content': iframe_playlist.dumps()})


def generate_m3u8_for_iframes():
    logging.info('generate_m3u8_for_iframes')
    """
    Generates an M3U8 object to be used for an I-frame playlist
    """
    result = m3u8.M3U8()
    result.media_sequence = 0
    result.version = '4'
    result.target_duration = 10
    result.playlist_type = 'vod'
    result.is_i_frames_only = True
    result.is_endlist = True
    return result


def create_iframe_segments(segment):
    logging.info('create_iframe_segments')
    """
    Takes a transport stream segment and returns I-frame segments for it
    """
    iframes, ts_data, packets_pos = get_segment_data(segment.absolute_uri)

    segment_bytes = 0
    segment_duration = 0
    iframe_segments = []

    for i, frame in enumerate(iframes):

        for j, pos in enumerate(packets_pos):

            if j < len(packets_pos) - 1 and frame[1] == pos:
                # We compared the output of our library to Apple's
                # example streams, and we were off by 188 bytes
                # for each I-frame byte-range.
                pkt_size = int(packets_pos[j+1]) - int(pos) + 188
                break
            else:
                pkt_size = frame[2]

        byterange = str(pkt_size) + '@' + frame[1]

        if i < len(iframes) - 1:
            extinf = float(iframes[i+1][0]) - float(frame[0])
        else:
            last_frame_time = ts_data[-1]['best_effort_timestamp_time']
            extinf = float(last_frame_time) - float(frame[0])

        segment_bytes += int(frame[2])
        segment_duration += extinf

        iframe_segments.append(m3u8.Segment(segment.uri,
                                            segment.base_uri,
                                            duration=extinf,
                                            byterange=byterange))

    return iframe_segments, segment_bytes, segment_duration


def get_segment_data(url):
    logging.info('get_segment_data')
    """
    Returns data about a transport stream.
    """
    all_data = json.loads(run_ffprobe(url))
    try:
        ts_data = all_data['packets_and_frames']
    except KeyError:
        raise DataError('Could not read TS data for %s' % url)

    iframes = []
    packets_pos = []

    for datum in ts_data:

        # we need to retrieve the time, position, and size of each I-frame
        if ('pkt_pos' in datum.keys() and 'pict_type' in datum.keys() and
                datum['pict_type'] == 'I' and datum['type'] == 'frame'):
            iframes.append((datum['best_effort_timestamp_time'],
                            datum['pkt_pos'], datum['pkt_size']))

        # we need to retrieve the position of each packet to be used
        # in calculating the actual size of each I-frame
        elif ('pos' in datum.keys() and datum['type'] == 'packet' and
              datum['codec_type'] == 'video'):
            packets_pos.append(datum['pos'])

    return iframes, ts_data, packets_pos


def run_ffprobe(filename):
    logging.info('run_ffprobe')
    """
    Runs an ffprobe on a transport stream and
    returns a string of the results in json format.
    """
    bash_cmd = ('ffprobe -print_format json -show_packets -show_frames ' +
                '%s 2> /dev/null')
    process = subprocess.Popen(bash_cmd % filename,
                               shell=True, stdout=subprocess.PIPE)
    out = process.stdout.read().strip()
    return out


def convert_codecs_for_iframes(codecs):
    logging.info('convert_codecs_for_iframes')
    """
    Takes a codecs string, converts it for iframes, and returns it
    """
    if codecs is not None:
        codecs_list = codecs.split(',')
        return ', '.join([k.strip() for k in codecs_list if 'avc1' in k])
    else:
        return None

def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', help="iframe playlist filename")
    parser.add_argument('input', nargs='+', help="m3u8 file")
    args = vars(parser.parse_args())

    if args["output"] == None:
        print "Output filename required"
        sys.exit(3)

    had_output = False
    for inputfile in args["input"]:
        outputfile = args["output"]
        logging.info('Processing M3U8: %s', inputfile)

        playlist_data = update_for_iframes(inputfile)
        logging.info('Processing completed.')

        logging.info('Saving master content...')
        f = open(outputfile, 'w')
        f.write(playlist_data['master_content'])
        f.close()

        dir = os.path.dirname(outputfile)
        logging.info('Saving iframe playlists | %s', dir)
        for playlist in playlist_data['iframe_playlists']:
            parsed = url_parser.urlparse(playlist['uri'])
            filename = dir + parsed.path
            logging.info('iframe playlist | %s', filename)
            if not os.path.exists(os.path.dirname(filename)):
                try:
                    os.makedirs(os.path.dirname(filename))
                except OSError as exc: # Guard against race condition
                    if exc.errno != errno.EEXIST:
                        raise
            f = open(filename, 'w')
            f.write(playlist['content'])
            f.close()

        logging.info('Completed!')

    if not had_output:
        sys.exit(4)

if __name__ == "__main__":
    main(sys.argv[1:])
