from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import math
import tensorflow as tf
import tensorflow.contrib.quantize as qg

from prepare import prepare_dataset, prepare_metrics, prepare_tfrecords


tf.app.flags.DEFINE_integer(
    'batch_size', 50, 'The number of samples in each batch.')
tf.app.flags.DEFINE_integer(
    'max_num_batches', None,
    'Max number of batches to evaluate by default use all.')
tf.app.flags.DEFINE_string(
    'dataset_name', 'imagenet', 'The name of the dataset to load.')
tf.app.flags.DEFINE_string(
    'dataset_split_name', 'test', 'The name of the train/test split.')
tf.app.flags.DEFINE_string(
    'dataset_dir', None, 'The directory where the dataset files are stored.')
tf.app.flags.DEFINE_string(
    'summary_dir', None, 'The directory where summaries save.')
tf.app.flags.DEFINE_string(
    'frozen_pb', None, 'The GraphDef file are stored with quantized_graph.')
tf.app.flags.DEFINE_string(
    'input_node_name', 'input', 'The name of the input node.')
tf.app.flags.DEFINE_string(
    'output_node_name', None, 'The name of the output node.')
tf.app.flags.DEFINE_integer(
    'input_size', 299, 'The width/height of the input image.')
tf.app.flags.DEFINE_string(
    'output_dir', None, 'The directory to save quantized graph and checkpoints.')
FLAGS = tf.app.flags.FLAGS


def load_graph_def(pb):
  with tf.gfile.GFile(pb, "rb") as f:
    graph_def = tf.GraphDef()
    graph_def.ParseFromString(f.read())
  return graph_def


def main(_):
  if not FLAGS.dataset_dir:
    raise ValueError('You must supply the dataset directory with --dataset_dir')
  if not FLAGS.frozen_pb:
    raise ValueError('You must supply the frozen pb with --frozen_pb')
  if not FLAGS.output_node_name:
    raise ValueError('You must supply the output node name with --output_node_name')
  if not FLAGS.output_dir:
    raise ValueError('You must supply the output directory with --output_dir')

  tf.logging.set_verbosity(tf.logging.INFO)
  tfrecords = prepare_tfrecords(FLAGS.dataset_name, FLAGS.dataset_dir,
                                FLAGS.dataset_split_name)

  if FLAGS.max_num_batches:
    num_batches = FLAGS.max_num_batches
  else:
    num_records = sum([len(list(tf.python_io.tf_record_iterator(r)))
                       for r in tfrecords])
    num_batches = int(math.ceil(num_records / float(FLAGS.batch_size)))

  tf.logging.info('Load GraphDef from frozen_pb {}'.format(FLAGS.frozen_pb))
  graph_def = load_graph_def(FLAGS.frozen_pb)

  tf.logging.info('Quantize Graph')
  with tf.Session() as sess:
    tf.import_graph_def(graph_def, name='')
    quantized_graph = qg.create_training_graph(sess.graph)
    quantized_inf_graph = qg.create_eval_graph(sess.graph)

  # Initialize `iterator` with training data.
  with tf.Session(graph=quantized_graph) as sess:
    tf.logging.info('Prepare dataset')
    with tf.name_scope("dataset"):
      filenames = tf.placeholder(tf.string, shape=[None])
      dataset = prepare_dataset(filenames, FLAGS.dataset_name, FLAGS.input_size,
                                batch_size=FLAGS.batch_size)
      iterator = dataset.make_initializable_iterator()
      next_batch = iterator.get_next()

    tf.logging.info('Prepare metrics')
    lbls, preds, accuracy, acc_update_op = prepare_metrics(FLAGS.dataset_name)

    tf.logging.info('Prepare Saver')
    saver = tf.train.Saver()

    if FLAGS.summary_dir:
      tf.logging.info('Prepare summary writer')
      summary_writer = tf.summary.FileWriter(FLAGS.summary_dir)

    # initialize
    sess.run(tf.global_variables_initializer())
    sess.run(tf.local_variables_initializer())
    sess.run(iterator.initializer, feed_dict={filenames: tfrecords})

    graph = sess.graph

    # get x and y
    x = graph.get_tensor_by_name('{}:0'.format(FLAGS.input_node_name))
    y = graph.get_tensor_by_name('{}:0'.format(FLAGS.output_node_name))

    # summary all min/max variables
    # print(graph.get_collection('variables')[3].eval())
    for var in graph.get_collection('variables'):
      tf.summary.scalar(var.name, var)
    summaries = tf.summary.merge_all()

    for step in range(num_batches):
      images, labels = sess.run(next_batch)
      ys = sess.run(y, feed_dict={x: images})
      sess.run(acc_update_op, feed_dict={lbls: labels, preds: ys})
      summary = sess.run(summaries)
      if FLAGS.summary_dir:
        summary_writer.add_summary(summary, step)

    print('Accuracy: [{:.4f}]'.format(sess.run(accuracy)))
    if FLAGS.summary_dir:
      summary_writer.add_graph(graph)

    # save graph and ckpts
    saver.save(sess, os.path.join(FLAGS.output_dir, "model.ckpt"))
    # tf.train.write_graph(graph, FLAGS.output_dir, 'quantor.pb', as_text=False)
    tf.train.write_graph(quantized_inf_graph, FLAGS.output_dir, 'quantor.pb', as_text=False)


if __name__ == '__main__':
  tf.app.run()
