import sys
from base import Model
import tensorflow as tf
from tensorflow.python.ops.rnn_cell import GRUCell
import numpy as np

class CharRNN(Model):
  def __init__(self, sess, vocab_size, batch_size=100,
               layer_depth=2, rnn_size=128, nce_samples=10, l2_reg_lambda=.2,
               seq_length=50, grad_clip=5., keep_prob=0.5,
               checkpoint_dir="checkpoint", dataset_name="wiki", infer=False):

    Model.__init__(self)

    if infer:
      batch_size = 1
      seq_length = 1

    self.sess = sess
    self.batch_size = batch_size
    self.seq_length = seq_length
    self.checkpoint_dir = checkpoint_dir
    self.dataset_name = dataset_name
    self.l2_reg_lambda = l2_reg_lambda

    # RNN
    self.rnn_size = rnn_size
    self.layer_depth = layer_depth
    self.grad_clip = grad_clip
    self.keep_prob = keep_prob

    cell = GRUCell(rnn_size)

    if not infer and self.keep_prob < 1:
      cell = tf.nn.rnn_cell.DropoutWrapper(cell, self.keep_prob)

    self.cell = cell = tf.nn.rnn_cell.MultiRNNCell([cell] * layer_depth, state_is_tuple=True)
    self.input_data = tf.placeholder(tf.int32, [batch_size, seq_length])
    self.targets = tf.placeholder(tf.int32, [batch_size, seq_length])
    self.initial_state = cell.zero_state(batch_size, tf.float32)

    # Keeping track of l2 regularization loss (optional)
    self.l2_penalized = tf.constant(0.0)

    with tf.variable_scope('rnnlm'):
      with tf.device("/cpu:0"):
        self.embedding = tf.get_variable(name="embedding",
                                         initializer=tf.random_uniform([vocab_size, rnn_size], -0.08, 0.08))

        inputs = tf.nn.embedding_lookup(self.embedding, self.input_data)

    with tf.variable_scope('decode'):
      softmax_w = tf.get_variable("softmax_w", [vocab_size, rnn_size],
                                  initializer=tf.contrib.layers.xavier_initializer(uniform=True))
      softmax_b = tf.get_variable("softmax_b", [vocab_size])

      # [batch_size, n_steps, rnn_hidden_size]
      outputs, self.final_state = tf.nn.dynamic_rnn(self.cell,
                                                    inputs,
                                                    time_major=False,
                                                    dtype=tf.float32)
      outputs = tf.reshape(outputs, [-1, rnn_size])
      self.logits = tf.matmul(outputs, softmax_w, transpose_b=True) + softmax_b
      self.probs = tf.nn.softmax(self.logits)

    self.global_step = tf.Variable(0, name='global_step', trainable=False)
    self.learning_rate = tf.Variable(0.0, trainable=False)

    train_labels = tf.reshape(self.targets, [-1, 1])
    self.loss = tf.nn.nce_loss(softmax_w,
                               softmax_b,
                               outputs,
                               tf.to_int64(train_labels),
                               nce_samples,
                               vocab_size)
    self.l2_penalized += tf.nn.l2_loss(softmax_w)
    self.l2_penalized += tf.nn.l2_loss(softmax_b)
    self.cost = (tf.reduce_sum(self.loss) / batch_size / seq_length) + l2_reg_lambda * self.l2_penalized

    tvars = tf.trainable_variables()
    optimizer = tf.train.AdamOptimizer(self.learning_rate)
    grads, _ = tf.clip_by_global_norm(tf.gradients(self.cost, tvars), grad_clip)
    self.train_op = optimizer.apply_gradients(zip(grads, tvars), global_step=self.global_step)

    tf.scalar_summary("learning rate", self.learning_rate)
    tf.scalar_summary("cost", self.cost)
    tf.histogram_summary("loss", self.loss)
    self.merged = tf.merge_all_summaries()

  def sample(self, sess, chars, vocab, num=200, prime='The '):
    self.initial_state = self.cell.zero_state(1, tf.float32)

    # assign final state to rnn
    state_list = []
    for state in self.initial_state:
      state_list.extend([state.eval()])

    prime = prime.decode('utf-8')

    for char in prime[:-1]:
      x = np.zeros((1, 1))
      x[0, 0] = vocab.get(char, 0)
      feed = {self.input_data: x}
      fetchs = []
      for i in range(len(self.initial_state)):
        state = self.initial_state[i]
        feed[state] = state_list[i]
      for state in self.final_state:
        fetchs.extend([state])
      state_list = sess.run(fetchs, feed)

    def weighted_pick(weights):
      t = np.cumsum(weights)
      s = np.sum(weights)
      return(int(np.searchsorted(t, np.random.rand(1)*s)))

    ret = prime
    char = prime[-1]

    for _ in xrange(num):
      x = np.zeros((1, 1))
      x[0, 0] = vocab.get(char, 0)
      feed = {self.input_data: x}
      fetchs = [self.probs]
      for i in range(len(self.initial_state)):
        state = self.initial_state[i]
        feed[state] = state_list[i]
      for state in self.final_state:
        fetchs.extend([state])
      res = sess.run(fetchs, feed)
      probs = res[0]
      state_list = res[1:]
      p = probs[0]
      # sample = int(np.random.choice(len(p), p=p))
      sample = weighted_pick(p)
      pred = chars[sample]
      ret += pred
      char = pred

    return ret
